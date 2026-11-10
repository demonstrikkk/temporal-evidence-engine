"""The settlement gate: locatable, about the value, and never a contradiction."""
from __future__ import annotations

from app.verify.decision import EvidenceFact, decide
from app.verify.schemas import ClaimStatus, ReasonCode, Relationship
from app.verify.settlement import (evidence_speaks_to, is_scope_block,
                                   settle_assertion)

PAGE = ("Acme Pro plan pricing. The Pro plan costs $99 per month, billed "
        "monthly. Enterprise is $499 per month. Contact sales@acme.test.")


def test_a_locatable_quote_about_the_value_settles():
    out = settle_assertion(subject="Acme", predicate="Pro plan price",
                           value_raw="$99/month",
                           quote="The Pro plan costs $99 per month", source_text=PAGE)
    assert out.ok, out.reason
    assert out.start is not None and out.end is not None
    assert PAGE[out.start:out.end] == "The Pro plan costs $99 per month"


def test_a_quote_that_does_not_mention_the_value_is_refused():
    """`Contact sales@acme.test` resolves in the page but says nothing about price."""
    out = settle_assertion(subject="Acme", predicate="Pro plan price",
                           value_raw="$99/month",
                           quote="Contact sales@acme.test", source_text=PAGE)
    assert not out.ok
    assert "amount" in out.reason


def test_a_quote_absent_from_the_stored_text_is_refused():
    out = settle_assertion(subject="Acme", predicate="Pro plan price",
                           value_raw="$99/month",
                           quote="The Pro plan costs $99 per month but this exact "
                                 "sentence is not stored", source_text=PAGE)
    assert not out.ok
    assert out.start is None


def test_a_different_scoped_value_is_a_scope_block_not_a_contradiction():
    result = is_scope_block(type("R", (), {"equivalent": False,
                                          "reason": "different_basis"})())
    assert result is True


def test_failed_support_does_not_become_a_contradiction():
    """The dangerous rule: "it did not support $99, so $99 is false"."""
    out = decide(
        claim_value="$99/month",
        answer_generated_at=None,
        facts=[EvidenceFact(evidence_id="e1", relationship=Relationship.CONTRADICTS,
                            quote_located=True, settled=False)],
    )
    assert out.status == ClaimStatus.UNVERIFIABLE
    assert out.reason == ReasonCode.NO_ENTAILMENT


def test_a_candidate_value_must_itself_be_mentioned_in_the_quote():
    ok, why = evidence_speaks_to("$129/month", "The Pro plan costs $99 per month")
    assert not ok
    assert "amount" in why


def test_currency_and_period_are_checked():
    ok, why = evidence_speaks_to("$99/year", "The Pro plan costs $99 per month")
    assert not ok
    assert "period" in why

    ok, why = evidence_speaks_to("99 EUR per month", "The Pro plan costs $99 per month")
    assert not ok
    assert "currency" in why
