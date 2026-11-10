"""The settlement gate: locatable, about the value, and never a contradiction."""
from __future__ import annotations

from app.verify.decision import EvidenceFact, decide
from app.verify.schemas import ClaimStatus, ReasonCode, Relationship
from app.verify.settlement import (evidence_speaks_to, is_scope_block,
                                   settle_assertion, subject_named)

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


def test_a_citation_marker_is_not_a_statement():
    """`[2013]` locates in the page and contains the number. It is not evidence.

    A live run returned it as the quote for a contradiction of "Notion was
    founded in 2013", so a footnote index refuted a true fact: two supporting
    sources, one bracket, and the verdict was `contradicted`.
    """
    for fragment in ("[2013]", "2013", "—", "[1][2]", "$99"):
        ok, why = evidence_speaks_to(2013, fragment)
        assert not ok, fragment
        assert "not a statement" in why, (fragment, why)


def test_a_contradiction_must_name_the_claim_subject():
    """A statement about another plan cannot refute this one.

    Live: "Business costs $24 monthly" was settled as a contradiction of
    "Notion Plus costs $10 per member per month", because the value gate checked
    that $24 appeared in the quote and never asked what the quote was about. One
    passage about a different plan outweighed four sources that agreed.
    """
    page = ("Notion pricing. The Plus plan costs $10 per member per month. "
            "Business costs $24 monthly or $20 per member per month.")
    wrong = settle_assertion(
        subject="Notion Plus", predicate="costs", value_raw="$24",
        quote="Business costs $24 monthly", source_text=page,
        must_name_subject=True)
    assert not wrong.ok
    assert "subject" in wrong.reason, wrong.reason

    # The same passage, read against the plan it is actually about, settles.
    right = settle_assertion(
        subject="Notion Business", predicate="costs", value_raw="$24",
        quote="Business costs $24 monthly", source_text=page,
        must_name_subject=True)
    assert right.ok, right.reason
    assert page[right.start:right.end] == "Business costs $24 monthly"


def test_a_citation_marker_is_refused_for_being_a_fragment_not_a_wrong_subject():
    """Both gates reject `[2013]`; the honest reason is that it is not a claim
    about anything, so the reader is told the passage is not evidence rather
    than that it is about another product."""
    page = "Notion history. Founded in 2013 by Ivan Zhao.[2013]"
    out = settle_assertion(subject="Notion", predicate="founded", value_raw="2013",
                           quote="[2013]", source_text=page,
                           must_name_subject=True)
    assert not out.ok
    assert "not a statement" in out.reason, out.reason


def test_a_per_member_price_support_settles_from_a_live_shaped_quote():
    """The exact quote shape that four live sources produced, and that used to
    be refused: `member` parsed as a per-unit but the detection table never
    learned it, so every support for a per-seat price was silently dropped."""
    page = ("Notion pricing. The Plus plan costs $10 per member per month "
            "(billed annually).")
    quote = "The Plus plan costs $10 per member per month (billed annually)."
    out = settle_assertion(subject="Notion Plus", predicate="costs",
                           value_raw="$10 per member per month",
                           quote=quote, source_text=page)
    assert out.ok, out.reason
    assert page[out.start:out.end] == quote


def test_a_billing_note_in_the_quote_does_not_change_the_price_period():
    ok, why = evidence_speaks_to("$10 per member per month",
                                 "Plus is $10 per member per month, billed annually")
    assert ok, why


def test_a_longer_number_does_not_contain_the_stated_amount():
    """`10` is a substring of `100`. That is not the same price."""
    ok, why = evidence_speaks_to("$10 per month", "Acme Pro costs $100 per month")
    assert not ok
    ok, why = evidence_speaks_to("$100 per month", "Acme Pro costs $100 per month")
    assert ok, why


def test_a_currency_symbol_attached_to_its_prefix_is_found():
    ok, why = evidence_speaks_to("USD 99 per month", "It costs US$99 per month")
    assert ok, why


def test_subject_matching_is_on_words_not_substrings():
    """`pro` must not be satisfied by `product`: that is a different fact."""
    named, why = subject_named("The product costs $99 per month", "Acme Pro")
    assert not named
    assert "subject" in why
    named, _ = subject_named("Acme Pro costs $99 per month", "Acme Pro")
    assert named
    # A passage may drop the brand on a page about it, as long as the qualifier
    # is there. Requiring the brand would refuse the most common support shape.
    named, _ = subject_named("The Pro plan costs $99 per month", "Acme Pro")
    assert named
