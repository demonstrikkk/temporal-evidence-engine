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


def test_a_different_currency_does_not_refute_without_a_support():
    """`€129/month` and `$99/month` are not comparable, so neither is refuted."""
    out = run([fact(rel=Relationship.CONTRADICTS, event=date(2026, 9, 1),
                    candidate="€129/month")])
    assert (out.status, out.reason, out.rule) == (
        ClaimStatus.UNVERIFIABLE, ReasonCode.SCOPE_MISMATCH, "R2b")


def test_a_different_per_unit_basis_does_not_refute_without_a_support():
    out = run([fact(rel=Relationship.CONTRADICTS, event=date(2026, 9, 1),
                    candidate="$99/month per seat")])
    assert out.reason == ReasonCode.SCOPE_MISMATCH


def test_a_same_scope_different_amount_still_refutes():
    out = run([fact(rel=Relationship.CONTRADICTS, event=date(2026, 9, 1),
                    candidate="$129/month")])
    assert (out.status, out.reason) == (ClaimStatus.CONTRADICTED,
                                        ReasonCode.CONTRADICTED_BY_SOURCE)


def test_a_textual_contradiction_has_no_scope_to_block():
    """A settled sentence that refutes a sentence is not held to money rules."""
    out = run([fact(rel=Relationship.CONTRADICTS, event=date(2026, 9, 1),
                    candidate="included in the Business and Enterprise plans")],
              value="included in all paid plans at no additional cost")
    assert out.status == ClaimStatus.CONTRADICTED


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


def test_r5_a_scope_change_cannot_manufacture_a_replacement():
    """A newer, incomparable value is not a replacement value.

    This used to abstain (`scope_mismatch`, rule R5). Since tev-1.2.0 the gate
    removes the passage before any rule runs, so the settled support stands and
    the incomparable contradiction is disclosed instead of deciding anything.
    The invariant the old assertion protected - never `changed` from a scope
    change - is still asserted, and is the point of the test.
    """
    out = run([
        fact("s", event=date(2026, 9, 1)),
        fact("c", rel=Relationship.CONTRADICTS, event=date(2026, 10, 2),
             candidate="$99/month per seat"),
    ])
    assert out.status != ClaimStatus.CHANGED
    assert out.replacement_id is None
    assert out.status == ClaimStatus.SUPPORTED
    assert out.material["scope_blocked"] == ["c"]


def test_a_different_scope_does_not_refute_a_claim_that_is_also_supported():
    """The live-run defect, rule R6 path (D-020).

    Claim: "Plus costs $10 per member per month". A passage that says "$8 per
    user per month ... a monthly subscription will cost you $10" was proposed as
    a contradiction, settled, and - because both event dates were unknown - R6
    turned it into `contradicted / temporal_ambiguity`. `compare` had already
    called the two values `different_basis`.
    """
    out = run([
        fact("s", status="missing"),
        fact("c", rel=Relationship.CONTRADICTS, status="missing",
             candidate="$8 per user per month"),
    ], value="$10 per member per month")
    assert out.status == ClaimStatus.SUPPORTED
    assert out.reason == ReasonCode.SUPPORTED_BY_SOURCE
    assert out.material["scope_blocked"] == ["c"]


def test_a_scope_mismatch_does_not_refute_even_without_a_support():
    """The same gate on the R3 path: a scope difference is never a refutation."""
    out = run([fact(rel=Relationship.CONTRADICTS, status="missing",
                    candidate="$8 per user per month")],
              value="$10 per member per month")
    assert out.status == ClaimStatus.UNVERIFIABLE
    assert out.reason == ReasonCode.SCOPE_MISMATCH


def test_a_compound_price_candidate_is_judged_on_its_clauses():
    """The exact string the live Mistral run produced (D-020).

    `compare` sees one `string` and answers `type_mismatch`; the clauses are two
    money values, both on a different per-unit basis, so neither can refute.
    Reading the whole sentence as "not comparable, therefore ignore" was what
    let this passage stand as a refutation in the first place.
    """
    out = run([
        fact("s", status="missing"),
        fact("c", rel=Relationship.CONTRADICTS, status="missing",
             candidate="$8 per user per month (annual billing) or $10 per user "
                       "per month (monthly billing)"),
    ], value="$10 per member per month")
    assert out.status == ClaimStatus.SUPPORTED
    assert out.material["scope_blocked"] == ["c"]


def test_a_compound_candidate_with_one_comparable_clause_still_refutes():
    """Half the sentence is incomparable, the other half genuinely differs."""
    out = run([
        fact("s", status="missing"),
        fact("c", rel=Relationship.CONTRADICTS, status="missing",
             candidate="$8 per user per month or $15 per member per month"),
    ], value="$10 per member per month")
    assert out.status == ClaimStatus.CONTRADICTED


def test_a_different_per_unit_basis_is_not_a_price_difference():
    """"$15 per month" is not comparable with "$10 per member per month".

    A workspace of five costs "$15 per month" less than five members at $10
    each, so the passage is not evidence that the claim is wrong. This is the
    comparator's existing reading of per-unit bases; the gate now applies it.
    """
    out = run([
        fact("s", status="missing"),
        fact("c", rel=Relationship.CONTRADICTS, status="missing",
             candidate="$15 per month"),
    ], value="$10 per member per month")
    assert out.status == ClaimStatus.SUPPORTED


def test_a_textual_refutation_is_not_blocked_by_the_scope_gate():
    """"free" states no comparable value; blocking it would invert the verdict."""
    out = run([
        fact("s", status="missing"),
        fact("c", rel=Relationship.CONTRADICTS, status="missing",
             candidate="free forever"),
    ], value="$10 per member per month")
    assert out.status == ClaimStatus.CONTRADICTED


def test_a_comparable_contradiction_still_produces_temporal_ambiguity():
    """The gate must not swallow a genuine refutation.

    Same shape as the case above, but `$129/month` is comparable with
    `$99/month`, so the contradiction survives the gate and R6 still refuses to
    invent an order from two unknown event dates.
    """
    out = run([
        fact("s", status="missing"),
        fact("c", rel=Relationship.CONTRADICTS, status="missing",
             candidate="$129/month"),
    ])
    assert out.status == ClaimStatus.CONTRADICTED
    assert out.reason == ReasonCode.TEMPORAL_AMBIGUITY
    assert out.rule == "R6"


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
