"""The deterministic verdict. No model output reaches this module.

Precedence, exactly as specified (BUILD-PLAN §8.1):

  R1 no locatable evidence            -> unverifiable / no_source
  R2 evidence, nothing entails it     -> unverifiable / no_entailment
  R3 contradiction, no support        -> contradicted
  R7 two primary sources disagree     -> unverifiable / conflicting_evidence
  R6 an event date involved is unknown-> contradicted / temporal_ambiguity
  R5 newer settled replacement        -> changed
  R4 contradiction older than support -> supported

`changed` has eight preconditions; every one is a test in
`backend/tests/verify/test_decision.py`. The two that are easiest to lose:

  * the original value must be *supported*, not merely "not contradicted";
  * the replacement must have an event date after the answer was generated.

A newer article is not a change. A publication date is not an event date.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.verify.schemas import ClaimStatus, ReasonCode, Relationship
from app.verify.settlement import SCOPE_REASONS, compare


@dataclass(frozen=True)
class EvidenceFact:
    """One candidate passage, already resolved against stored text.

    `quote_located` and `settled` are separate on purpose: a quote can be found
    in the source without establishing the claim. Only `settled` rows count
    towards a verdict.
    """

    evidence_id: str
    relationship: Relationship
    quote_located: bool = False
    settled: bool = False
    event_date: date | None = None
    event_date_status: str = "missing"
    candidate_value: Any = None
    publication_date: date | None = None
    credibility: str = "secondary"          # primary | secondary
    retrieved_at: datetime | None = None


@dataclass(frozen=True)
class Decision:
    status: ClaimStatus
    reason: ReasonCode
    rule: str
    explanation: str
    supporting: tuple[str, ...] = ()
    contradicting: tuple[str, ...] = ()
    replacement_id: str | None = None
    material: dict[str, Any] = field(default_factory=dict)


def _ids(facts: list[EvidenceFact]) -> tuple[str, ...]:
    return tuple(f.evidence_id for f in facts)


def _date_of(fact: EvidenceFact) -> date | None:
    return fact.event_date


def _newest(facts: list[EvidenceFact]) -> EvidenceFact:
    return max(facts, key=lambda f: (f.event_date or date.min))


def decide(*, claim_value: Any, answer_generated_at: datetime | None,
           facts: list[EvidenceFact]) -> Decision:
    """Assign the status from stored rows only."""
    considered = [f for f in facts if f.relationship != Relationship.SILENT]
    locatable = [f for f in considered if f.quote_located]

    # R1 — nothing a reader could check.
    if not locatable:
        return Decision(ClaimStatus.UNVERIFIABLE, ReasonCode.NO_SOURCE, "R1",
                        "No source with a locatable quote was found for this claim.")

    counted = [f for f in locatable if f.settled]
    supports = [f for f in counted if f.relationship == Relationship.SUPPORTS]
    contradicts = [f for f in counted if f.relationship == Relationship.CONTRADICTS]

    # R2 — sources exist that discuss the claim, but none establishes it.
    if not supports and not contradicts:
        return Decision(
            ClaimStatus.UNVERIFIABLE, ReasonCode.NO_ENTAILMENT, "R2",
            "Sources discuss this claim, but none establishes it. "
            "A mention is not evidence.", supporting=_ids(supports),
            contradicting=_ids(contradicts))

    # R3 — a settled contradiction with no settled support.
    if contradicts and not supports:
        return Decision(ClaimStatus.CONTRADICTED,
                        ReasonCode.CONTRADICTED_BY_SOURCE, "R3",
                        "A located, settled passage directly refutes this claim.",
                        contradicting=_ids(contradicts))

    # R7 — two primary sources disagree at the same effective time.
    if supports and contradicts:
        sup_date = max((f.event_date for f in supports if f.event_date), default=None)
        con_date = max((f.event_date for f in contradicts if f.event_date), default=None)
        if (sup_date is not None and sup_date == con_date
                and all(f.credibility == "primary" for f in supports + contradicts)):
            return Decision(
                ClaimStatus.UNVERIFIABLE, ReasonCode.CONFLICTING_EVIDENCE, "R7",
                "Two primary sources state incompatible values for the same "
                "effective date. The conflict is not resolved by preferring one.",
                supporting=_ids(supports), contradicting=_ids(contradicts))

        # R6 — ordering needs event dates; without them, do not invent an order.
        if any(f.event_date is None for f in supports + contradicts):
            return Decision(
                ClaimStatus.CONTRADICTED, ReasonCode.TEMPORAL_AMBIGUITY, "R6",
                "Support and contradiction both exist, but at least one relevant "
                "event date is unknown, so neither can be shown to come first.",
                supporting=_ids(supports), contradicting=_ids(contradicts))

        newer_support = _newest(supports)
        newer_contra = _newest(contradicts)

        # R5 — the contradiction is newer than the support and states a
        # replacement value effective after the answer was generated.
        if (_date_of(newer_contra) and _date_of(newer_support)
                and _date_of(newer_contra) > _date_of(newer_support)):
            replacement = newer_contra
            if replacement.candidate_value is None:
                return Decision(
                    ClaimStatus.CONTRADICTED, ReasonCode.CONTRADICTED_BY_SOURCE,
                    "R3",
                    "A newer passage contradicts the claim but states no "
                    "replacement value, so no change can be established.",
                    supporting=_ids(supports), contradicting=_ids(contradicts))
            cmp = compare(claim_value, replacement.candidate_value)
            if cmp.equivalent:
                return Decision(
                    ClaimStatus.UNVERIFIABLE, ReasonCode.CONFLICTING_EVIDENCE, "R7",
                    "A passage marked as a contradiction states a value "
                    "equivalent to the claim; the metadata and the value disagree.",
                    supporting=_ids(supports), contradicting=_ids(contradicts))
            if cmp.reason in SCOPE_REASONS:
                return Decision(
                    ClaimStatus.UNVERIFIABLE, ReasonCode.SCOPE_MISMATCH, "R5",
                    f"The replacement value cannot be compared with the claim "
                    f"({cmp.reason}); a change is not established.",
                    supporting=_ids(supports), contradicting=_ids(contradicts))
            if answer_generated_at is None:
                return Decision(
                    ClaimStatus.CONTRADICTED, ReasonCode.GENERATION_TIME_UNKNOWN,
                    "R5",
                    "The value changed, but the answer's generation time is "
                    "unknown, so it cannot be shown to have changed afterwards.",
                    supporting=_ids(supports), contradicting=_ids(contradicts))
            if _date_of(replacement) and _date_of(replacement) > answer_generated_at.date():
                return Decision(
                    ClaimStatus.CHANGED, ReasonCode.VALUE_CHANGED, "R5",
                    "The claim was supported for the answer's generation time, "
                    "and a later settled source states a replacement value with "
                    "an event date after generation.",
                    supporting=_ids(supports), contradicting=_ids(contradicts),
                    replacement_id=replacement.evidence_id,
                    material={"replacement_value": replacement.candidate_value,
                              "event_date": _date_of(replacement).isoformat(),
                              "generated_at": answer_generated_at.isoformat()})
            return Decision(
                ClaimStatus.CONTRADICTED, ReasonCode.CONTRADICTED_BY_SOURCE, "R3",
                "The newest settled passage refutes the claim, but the change "
                "is not shown to have happened after the answer was generated.",
                supporting=_ids(supports), contradicting=_ids(contradicts))

        # R4 — the supporting state is newer than the contradiction.
        return Decision(ClaimStatus.SUPPORTED, ReasonCode.SUPPORTED_BY_SOURCE, "R4",
                        "A settled, locatable passage supports the claim, and "
                        "any contradiction predates it.",
                        supporting=_ids(supports), contradicting=_ids(contradicts))

    # Only support remains.
    return Decision(ClaimStatus.SUPPORTED, ReasonCode.SUPPORTED_BY_SOURCE, "R4",
                    "A settled, locatable passage supports the claim.",
                    supporting=_ids(supports))
