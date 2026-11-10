"""Query planning is deterministic, capped, and derived from the claim."""
from __future__ import annotations

from app.research.planner import plan_queries
from app.verify.schemas import ClaimDraft


def claim(**overrides) -> ClaimDraft:
    base = dict(claim_text="Acme Pro costs $99 per month",
                subject="Acme Pro", predicate="price", object_raw="$99/month")
    base.update(overrides)
    return ClaimDraft(**base)


def test_the_subject_and_predicate_come_first():
    queries = plan_queries(claim())
    assert queries[0] == "Acme Pro price"


def test_queries_are_deduplicated_and_bounded():
    queries = plan_queries(claim(claim_text="Acme Pro price", subject="Acme Pro",
                                 predicate="price", object_raw=""))
    assert len(queries) == len(set(queries))
    assert len(queries) <= 3


def test_a_claim_with_no_fields_still_produces_one_query():
    queries = plan_queries(claim(subject="", predicate=""))
    assert queries == ["Acme Pro costs $99 per month"]


def test_a_very_short_claim_produces_no_query():
    assert plan_queries(claim(claim_text="abc", subject="", predicate="",
                              object_raw="")) == []
