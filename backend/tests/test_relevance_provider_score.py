"""The search engine's relevance is a bounded prior, not a verdict.

Tavily returns a `score` in [0, 1] and the adapter used to drop it, so ranking
saw only the URL and title. It is used now — but capped below one field hit, so
a confident engine score can never promote a popular page over one that names
the entity the plan asked for.
"""
from __future__ import annotations

from app.agents.graph import _plan_relevance

PLAN = {"entity": "acme", "fields": [{"name": "employee_count"}],
        "queries": ["acme employee count"]}


def test_an_entity_hit_outranks_a_perfect_engine_score():
    entity_hit = {"url": "https://acme.example/about", "title": "Acme"}
    engine_favourite = {"url": "https://x.example/a", "title": "Company profile",
                        "score": 1.0}
    assert _plan_relevance(entity_hit, PLAN) > _plan_relevance(engine_favourite, PLAN)


def test_engine_score_breaks_ties_between_otherwise_equal_candidates():
    low = {"url": "https://x.example/a", "title": "Company profile", "score": 0.1}
    high = {"url": "https://x.example/a", "title": "Company profile", "score": 0.9}
    assert _plan_relevance(high, PLAN) > _plan_relevance(low, PLAN)


def test_missing_or_garbage_score_is_zero_not_a_crash():
    base = {"url": "https://x.example/a", "title": "Company profile"}
    zero = {**base, "score": 0.0}
    garbage = {**base, "score": "not-a-number"}
    assert _plan_relevance(base, PLAN) == _plan_relevance(zero, PLAN)
    assert _plan_relevance(garbage, PLAN) == _plan_relevance(zero, PLAN)


def test_a_publication_date_is_only_a_tie_break():
    base = {"url": "https://x.example/a", "title": "Company profile", "score": 0.5}
    dated = {**base, "published_date": "2024-03-01"}
    entity_hit = {"url": "https://acme.example/about", "title": "Acme"}
    assert _plan_relevance(dated, PLAN) > _plan_relevance(base, PLAN)
    assert _plan_relevance(dated, PLAN) < _plan_relevance(entity_hit, PLAN)
