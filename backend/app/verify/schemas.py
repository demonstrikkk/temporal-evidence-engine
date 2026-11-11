"""Domain models for the Temporal Evidence Engine.

These are the contract between the API, the orchestrator, the storage layer and
the UI. Two rules shape them:

* **Statuses are closed sets.** A claim is `pending`, `supported`, `changed`,
  `contradicted` or `unverifiable`. `settle` refuses a seventh status; so does
  this module. An unrecognised string on the wire becomes `unverifiable`, the
  least-committal true statement, never a guess.
* **Four timestamps, four fields.** `generated_at` (answer), `published_date`
  (source), `event_date` (the fact) and `retrieved_at` (us) are not
  interchangeable and never collapse into one `date`.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ClaimStatus(str, Enum):
    PENDING = "pending"
    SUPPORTED = "supported"
    CHANGED = "changed"
    CONTRADICTED = "contradicted"
    UNVERIFIABLE = "unverifiable"

    @classmethod
    def parse(cls, value: object) -> "ClaimStatus":
        try:
            return cls(str(value))
        except ValueError:
            return cls.UNVERIFIABLE


class ReasonCode(str, Enum):
    NO_SOURCE = "no_source"
    NO_ENTAILMENT = "no_entailment"
    SUPPORTED_BY_SOURCE = "supported_by_source"
    VALUE_CHANGED = "value_changed_after_generation"
    CONTRADICTED_BY_SOURCE = "contradicted_by_source"
    TEMPORAL_AMBIGUITY = "temporal_ambiguity"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    MISSING_EVENT_DATE = "missing_event_date"
    SCOPE_MISMATCH = "scope_mismatch"
    EXTRACTION_FAILED = "extraction_failed"
    GENERATION_TIME_UNKNOWN = "generation_time_unknown"
    NO_CLAIMS = "no_claims"


class Relationship(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    MENTIONS_ONLY = "mentions_only"
    SILENT = "silent"

    @classmethod
    def parse(cls, value: object) -> "Relationship":
        try:
            return cls(str(value))
        except ValueError:
            # A model that returns something else did not establish a
            # relationship. Silence is the honest default.
            return cls.SILENT


class ClaimType(str, Enum):
    FACTUAL = "factual"
    TIME_SENSITIVE = "time_sensitive"
    STABLE = "stable_factual"
    OPINION = "opinion"
    PREDICTION = "prediction"
    AMBIGUOUS = "ambiguous"


class Volatility(str, Enum):
    EPHEMERAL = "ephemeral"
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    STABLE = "stable"

    @classmethod
    def parse(cls, value: object) -> "Volatility":
        try:
            return cls(str(value))
        except ValueError:
            return cls.MODERATE


#: Hours until freshness halves. Product heuristics, not measured constants.
#: Stable 30 d, low 7 d, moderate 24 h, high 6 h, ephemeral 15 min (WP-5b).
HALF_LIFE_HOURS: dict[Volatility, float] = {
    Volatility.EPHEMERAL: 0.25,
    Volatility.HIGH: 6,
    Volatility.MODERATE: 24,
    Volatility.LOW: 7 * 24,
    Volatility.STABLE: 30 * 24,
}

#: Claim types that are never turned into a verification verdict. An opinion is
#: not a factual claim that failed; it is not a factual claim at all.
NON_VERIFIABLE_TYPES = {ClaimType.OPINION, ClaimType.PREDICTION}


class AnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=200_000)
    prompt: str = Field(default="", max_length=20_000)
    generated_at: datetime | None = None
    generated_at_source: Literal["user_supplied", "unknown"] = "unknown"
    source_urls: list[str] = Field(default_factory=list, max_length=20)
    domain_filter: list[str] = Field(default_factory=list, max_length=20)
    skip_non_factual: bool = True
    locale: str = "en"
    budget_mode: Literal["balanced", "conserve", "deep", "offline"] = "balanced"

    @field_validator("generated_at_source", mode="before")
    @classmethod
    def _source(cls, value: object) -> str:
        return "user_supplied" if str(value or "") == "user_supplied" else "unknown"

    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        # A timestamp with no stated provenance is not a timestamp we may use to
        # prove a change happened after generation.
        if self.generated_at is None:
            object.__setattr__(self, "generated_at_source", "unknown")
        elif self.generated_at_source == "unknown":
            object.__setattr__(self, "generated_at_source", "user_supplied")


class ClaimDraft(BaseModel):
    """One atomic, independently checkable proposition."""

    claim_text: str = Field(min_length=1, max_length=2_000)
    subject: str = ""
    predicate: str = ""
    object_raw: str = ""
    object_normalized: dict[str, Any] = Field(default_factory=dict)
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    claim_type: ClaimType = ClaimType.FACTUAL
    time_sensitive: bool = False
    volatility_class: Volatility = Volatility.MODERATE
    temporal_scope: dict[str, Any] = Field(default_factory=dict)
    extraction_confidence: float = Field(default=0.0, ge=0, le=1)
    span_start: int = -1
    span_end: int = -1


class ClaimExtraction(BaseModel):
    claims: list[ClaimDraft] = Field(default_factory=list)


class ClaimOut(BaseModel):
    id: str
    answer_id: str
    run_id: str
    ordinal: int
    claim_text: str
    span_start: int
    span_end: int
    subject: str
    predicate: str
    object_raw: str
    object_normalized: dict[str, Any]
    qualifiers: dict[str, Any]
    claim_type: ClaimType
    time_sensitive: bool
    volatility_class: Volatility
    status: ClaimStatus
    reason_code: ReasonCode | None = None
    next_check_at: datetime | None = None
    last_verified_at: datetime | None = None
    freshness_index: int | None = None
    evidence: list["EvidenceOut"] = Field(default_factory=list)


class EvidenceOut(BaseModel):
    id: str
    claim_id: str
    quote_text: str
    start_char: int
    end_char: int
    source_url: str
    source_title: str
    publisher: str
    published_date: date | None = None
    published_date_source: str = "unknown"
    event_date: date | None = None
    event_date_status: Literal["explicit", "relative", "missing"] = "missing"
    event_date_quote: str | None = None
    retrieved_at: datetime
    extraction_method: str
    relationship_proposed: str
    relationship_settled: str | None = None
    settlement_status: str | None = None
    settlement_reason: str | None = None
    candidate_value_raw: str | None = None
    candidate_value_normalized: dict[str, Any] | None = None
    credited: bool = False


class ClaimDetail(ClaimOut):
    rule: str | None = None
    explanation: str = ""
    value_diff: dict[str, Any] | None = None
    timeline: list[dict[str, Any]] = Field(default_factory=list)


class RunOut(BaseModel):
    id: str
    answer_id: str
    status: str
    stage: str
    rule_version: str
    cache_mode: str
    started_at: datetime
    completed_at: datetime | None = None
    error: dict[str, Any] | None = None
    answer: dict[str, Any] | None = None
    claims: list[ClaimOut] = Field(default_factory=list)
    counters: dict[str, Any] = Field(default_factory=dict)
    gateway: dict[str, Any] = Field(default_factory=dict)


class RunCreated(BaseModel):
    run_id: str
    answer_id: str
    status: str = "queued"


ClaimOut.model_rebuild()
