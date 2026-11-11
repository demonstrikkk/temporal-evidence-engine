"""What the answer asserted, and what the run actually checked.

The extractor is a model, and a model that returns two claims from a paragraph
of eleven assertions is not lying — it is answering the question it was asked
with the budget it was given. The failure is that nobody could *see* the other
nine. This module makes them countable.

The audit is deterministic end to end:

1. split the answer into sentences (`segment`);
2. find every checkable value in those sentences (`assertions.candidates`);
3. a candidate is **checked** when a stored claim span contains it;
4. a claim that contains no candidate is still an assertion — it was extracted,
   so it will be verified, and it belongs in the denominator;
5. anything left is given a disposition from the sentence's own words:
   `subjective`, `prediction`, or the admission `not_checked`.

Two ratios are reported, and they answer different questions. `coverage` is
checked / every assertion. `material_coverage` removes the assertions that no
source could settle (opinions, predictions) from the denominator, because a
system is not incomplete for declining to verify a preference.

None of this touches `claims.status`. A coverage disposition is a statement
about the extraction, not a verdict about a fact, and putting it in the status
column would break both the four-status contract and the single promotion
chokepoint in `validator.settle`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.verify.assertions import disposition_for, sentence_candidates
from app.verify.segment import Sentence, segment

#: In report order. `checked` first because it is the only one that means the
#: product did its job.
DISPOSITIONS: tuple[str, ...] = ("checked", "subjective", "prediction",
                                 "not_checked")
#: Dispositions that excuse an assertion from the material denominator.
EXCUSED: frozenset[str] = frozenset({"subjective", "prediction"})


@dataclass(frozen=True)
class AssertionItem:
    """One assertion the reader could check, and what happened to it."""

    kind: str
    text: str
    start: int
    end: int
    sentence_start: int
    sentence_end: int
    disposition: str
    signals: dict[str, bool] = field(default_factory=dict)
    claim_id: str | None = None

    def as_row(self, *, run_id: str, answer_id: str) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "answer_id": answer_id,
            "claim_id": self.claim_id,
            "kind": self.kind,
            "text": self.text,
            "start_char": self.start,
            "end_char": self.end,
            "sentence_start": self.sentence_start,
            "sentence_end": self.sentence_end,
            "disposition": self.disposition,
            "signals": self.signals,
        }


@dataclass
class CoverageReport:
    """The audit's result. Stored as a row; shown as a section of the report."""

    answer_chars: int = 0
    sentences: int = 0
    candidates: int = 0
    claims: int = 0
    #: Claims whose span contained no regex candidate. They are counted as
    #: assertions in their own right, which is what stops "we extracted 10
    #: claims" from looking complete when all ten came from one sentence.
    claims_without_candidate: int = 0
    total_assertions: int = 0
    checked: int = 0
    dispositions: dict[str, int] = field(default_factory=dict)
    items: list[AssertionItem] = field(default_factory=list)
    #: Sentences holding at least one `not_checked` candidate and no claim.
    repair_targets: list[dict[str, Any]] = field(default_factory=list)
    repair_calls: int = 0
    repaired_claims: int = 0

    # -- ratios -----------------------------------------------------------
    @property
    def coverage(self) -> float:
        """Checked / every assertion. Reported to one decimal place."""
        if not self.total_assertions:
            return 1.0
        return self.checked / self.total_assertions

    @property
    def material_assertions(self) -> int:
        return self.total_assertions - sum(self.dispositions.get(name, 0)
                                           for name in EXCUSED)

    @property
    def material_coverage(self) -> float:
        if not self.material_assertions:
            return 1.0
        return self.checked / self.material_assertions

    @property
    def uncovered(self) -> int:
        return self.dispositions.get("not_checked", 0)

    def pct(self, value: float) -> str:
        return f"{value * 100:.1f}%"

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer_chars": self.answer_chars,
            "sentences": self.sentences,
            "candidates": self.candidates,
            "claims": self.claims,
            "claims_without_candidate": self.claims_without_candidate,
            "total_assertions": self.total_assertions,
            "checked": self.checked,
            "coverage": round(self.coverage, 4),
            "material_assertions": self.material_assertions,
            "material_coverage": round(self.material_coverage, 4),
            "dispositions": dict(self.dispositions),
            "uncovered": self.uncovered,
            "repair_calls": self.repair_calls,
            "repaired_claims": self.repaired_claims,
            "repair_targets": list(self.repair_targets),
        }

    def summary(self) -> str:
        return (
            f"{self.checked}/{self.total_assertions} assertions checked "
            f"({self.pct(self.coverage)}); "
            f"material {self.pct(self.material_coverage)}; "
            f"{self.uncovered} uncovered"
        )


def _claim_spans(claims: Iterable[dict[str, Any]]) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for claim in claims:
        try:
            start = int(claim.get("span_start"))
            end = int(claim.get("span_end"))
        except (TypeError, ValueError):
            continue
        if start < 0 or end <= start:
            continue
        spans.append((start, end, str(claim.get("id") or "")))
    return spans


def _covering_claim(start: int, end: int,
                    spans: list[tuple[int, int, str]]) -> str | None:
    """The claim whose collected span contains this candidate, if any.

    Containment, not overlap: a claim of "the tower is 330 metres tall" does not
    cover a second value elsewhere in the same sentence. Overlap would let one
    claim absorb every number in a coordinate clause and report coverage the run
    never achieved.
    """
    for claim_start, claim_end, claim_id in spans:
        if claim_start <= start and end <= claim_end:
            return claim_id or None
    return None


def audit(answer: str, claims: Iterable[dict[str, Any]]) -> CoverageReport:
    """Compute the report over the answer's own text and the stored claims."""
    claim_list = list(claims)
    spans = _claim_spans(claim_list)
    sentences: list[Sentence] = segment(answer or "")

    items: list[AssertionItem] = []
    counts: dict[str, int] = {name: 0 for name in DISPOSITIONS}
    covered_claims: set[str] = set()

    for sentence in sentences:
        for candidate in sentence_candidates(sentence.start, sentence.end,
                                             sentence.text):
            claim_id = _covering_claim(candidate.start, candidate.end, spans)
            if claim_id:
                covered_claims.add(claim_id)
                disposition = "checked"
            else:
                disposition = disposition_for(sentence.text, covered=False)
            counts[disposition] = counts.get(disposition, 0) + 1
            items.append(AssertionItem(
                kind=candidate.kind, text=candidate.text,
                start=candidate.start, end=candidate.end,
                sentence_start=sentence.start, sentence_end=sentence.end,
                disposition=disposition, claim_id=claim_id,
                signals=_signals(sentence.text)))

    # A claim with no candidate inside it is an assertion too. Counting only
    # regex hits would report 100% coverage for an answer whose every claim was
    # a bare relation, which is exactly the shape that hid the Eiffel gap.
    claims_without_candidate = sum(1 for _start, _end, claim_id in spans
                                   if claim_id not in covered_claims)
    candidate_count = len(items)
    checked_candidates = counts.get("checked", 0)
    total = candidate_count + claims_without_candidate
    checked = checked_candidates + claims_without_candidate

    targets = _repair_targets(items)
    report = CoverageReport(
        answer_chars=len(answer or ""),
        sentences=len(sentences),
        candidates=candidate_count,
        claims=len(claim_list),
        claims_without_candidate=claims_without_candidate,
        total_assertions=total,
        checked=checked,
        dispositions=counts,
        items=items,
        repair_targets=targets,
    )
    return report


def _signals(text: str) -> dict[str, bool]:
    from app.verify.assertions import signals
    return signals(text).as_dict()


def _repair_targets(items: list[AssertionItem]) -> list[dict[str, Any]]:
    """Sentences with an uncovered candidate, deduplicated and in order.

    A sentence that also contains a covered value remains a target for its
    uncovered value; the orchestrator deduplicates repair claims against the
    claims already stored.
    """
    by_sentence: dict[tuple[int, int], list[AssertionItem]] = {}
    for item in items:
        if item.disposition != "not_checked":
            continue
        by_sentence.setdefault((item.sentence_start, item.sentence_end),
                               []).append(item)
    targets: list[dict[str, Any]] = []
    for (start, end), found in sorted(by_sentence.items()):
        targets.append({
            "start": start,
            "end": end,
            "candidates": [item.text for item in found],
        })
    return targets


def apply_repair(report: CoverageReport, answer: str,
                 claims: Iterable[dict[str, Any]], *,
                 calls: int = 1) -> CoverageReport:
    """Recompute after repair claims were stored, keeping the call count.

    The audit is recomputed from the same deterministic inputs, so a repair that
    failed to cover anything simply produces the same numbers plus an honest
    `repair_calls` of 1. Nothing is interpolated to make the number look better.
    """
    fresh = audit(answer, claims)
    fresh.repair_calls = report.repair_calls + calls
    fresh.repaired_claims = report.repaired_claims
    return fresh
