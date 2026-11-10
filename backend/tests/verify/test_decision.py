"""The precedence table, one test per rule, plus the eight `changed` gates."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.verify.decision import EvidenceFact, decide
from app.verify.schemas import ClaimStatus, ReasonCode, Relationship

GEN = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def fact(eid="e1", rel=Relationship.SUPPORTS, *, located=True, settled=True,
         event=None, status="explicit", candidate=None, cred="secondary") -> EvidenceFact:
    return EvidenceFact(evidence_id=eid, relationship=rel, quote_located=located,
                        settled=settled, event_date=event, event_date_status=status,
                        candidate_value=candidate, credibility=cred)


def run(facts, *, value="$99/month", gen=GEN):
    return decide(claim_value=value, answer_generated_at=gen, facts=facts)


def test_r1_no_locatable_evidence():
    out = run([fact(located=False)])
    assert (out.status, out.reason, out.rule) == (
        ClaimStatus.UNVERIFIABLE, ReasonCode.NO_SOURCE, "R1")


def test_r2_a_mention_is_not_entailment():
    out = run([fact(rel=Relationship.MENTIONS_ONLY, settled=False)])
    assert (out.status, out.reason) == (ClaimStatus.UNVERIFIABLE, ReasonCode.NO_ENTAILMENT)


def test_r2_an_unsettled_support_does_not_count():
    out = run([fact(settled=False)])
    assert out.reason == ReasonCode.NO_ENTAILMENT


def test_r3_contradiction_without_support():
    out = run([fact(rel=Relationship.CONTRADICTS, event=date(2026, 9, 1),
                    candidate="$129/month")])
    assert (out.status, out.reason) == (ClaimStatus.CONTRADICTED,
                                        ReasonCode.CONTRADICTED_BY_SOURCE)


def test_r4_support_with_an_older_contradiction():
    out = run([
        fact("s", event=date(2026, 10, 1)),
        fact("c", rel=Relationship.CONTRADICTS, event=date(2026, 9, 1),
             candidate="$129/month"),
    ])
    assert (out.status, out.reason, out.rule) == (
        ClaimStatus.SUPPORTED, ReasonCode.SUPPORTED_BY_SOURCE, "R4")


def test_r5_changed_needs_a_replacement_after_generation():
    out = run([
        fact("s", event=date(2026, 9, 1)),
        fact("c", rel=Relationship.CONTRADICTS, event=date(2026, 10, 2),
             candidate="$129/month"),
    ])
    assert (out.status, out.reason, out.rule) == (
        ClaimStatus.CHANGED, ReasonCode.VALUE_CHANGED, "R5")
    assert out.replacement_id == "c"
    assert out.material["event_date"] == "2026-10-02"


def test_r5_a_change_before_generation_is_not_a_change():
    out = run([
        fact("s", event=date(2026, 9, 1)),
        fact("c", rel=Relationship.CONTRADICTS, event=date(2026, 9, 20),
             candidate="$129/month"),
    ])
    assert out.status == ClaimStatus.CONTRADICTED
    assert out.reason == ReasonCode.CONTRADICTED_BY_SOURCE


def test_r5_unknown_generation_time_cannot_produce_changed():
    out = run([
        fact("s", event=date(2026, 9, 1)),
        fact("c", rel=Relationship.CONTRADICTS, event=date(2026, 10, 2),
             candidate="$129/month"),
    ], gen=None)
    assert out.status == ClaimStatus.CONTRADICTED
    assert out.reason == ReasonCode.GENERATION_TIME_UNKNOWN


def test_r5_a_scope_change_is_an_abstention_not_a_replacement():
    out = run([
        fact("s", event=date(2026, 9, 1)),
        fact("c", rel=Relationship.CONTRADICTS, event=date(2026, 10, 2),
             candidate="$99/month per seat"),
    ])
    assert out.status == ClaimStatus.UNVERIFIABLE
    assert out.reason == ReasonCode.SCOPE_MISMATCH


def test_r5_an_equivalent_candidate_is_a_metadata_conflict():
    out = run([
        fact("s", event=date(2026, 9, 1)),
        fact("c", rel=Relationship.CONTRADICTS, event=date(2026, 10, 2),
             candidate="99 USD per month"),
    ])
    assert out.status == ClaimStatus.UNVERIFIABLE
    assert out.reason == ReasonCode.CONFLICTING_EVIDENCE


def test_r5_a_contradiction_without_a_value_is_not_a_change():
    out = run([
        fact("s", event=date(2026, 9, 1)),
        fact("c", rel=Relationship.CONTRADICTS, event=date(2026, 10, 2)),
    ])
    assert out.status == ClaimStatus.CONTRADICTED


def test_r6_unknown_event_dates_do_not_invent_an_order():
    out = run([
        fact("s", event=None, status="missing"),
        fact("c", rel=Relationship.CONTRADICTS, event=date(2026, 10, 2),
             candidate="$129/month"),
    ])
    assert (out.status, out.reason, out.rule) == (
        ClaimStatus.CONTRADICTED, ReasonCode.TEMPORAL_AMBIGUITY, "R6")


def test_r7_two_primary_sources_at_the_same_time_abstain():
    out = run([
        fact("s", event=date(2026, 10, 1), cred="primary"),
        fact("c", rel=Relationship.CONTRADICTS, event=date(2026, 10, 1),
             candidate="$129/month", cred="primary"),
    ])
    assert (out.status, out.reason, out.rule) == (
        ClaimStatus.UNVERIFIABLE, ReasonCode.CONFLICTING_EVIDENCE, "R7")


def test_a_secondary_contradiction_does_not_override_a_primary_support():
    out = run([
        fact("s", event=date(2026, 10, 1), cred="primary"),
        fact("c", rel=Relationship.CONTRADICTS, event=date(2026, 10, 1),
             candidate="$129/month", cred="secondary"),
    ])
    assert out.status == ClaimStatus.SUPPORTED


def test_silent_rows_are_ignored_entirely():
    out = run([fact(rel=Relationship.SILENT, located=False)])
    assert out.reason == ReasonCode.NO_SOURCE


@pytest.mark.parametrize("value,candidate", [
    ("$99/month", "$99 per month"),          # equivalent formatting
])
def test_an_equivalent_candidate_never_becomes_a_change(value, candidate):
    out = run([
        fact("s", event=date(2026, 9, 1)),
        fact("c", rel=Relationship.CONTRADICTS, event=date(2026, 10, 2),
             candidate=candidate),
    ], value=value)
    assert out.status != ClaimStatus.CHANGED
