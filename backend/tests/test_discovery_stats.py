"""Bill what discovery issued, not what the plan projected.

A fatal search refusal short-circuits discovery, so the projection (queries the
plan intended) overcharged for calls that never happened. `discover` now reports
its own count and the runner bills that number.
"""
from __future__ import annotations

import asyncio

from app.core.errors import AppError
from app.services import discovery
from app.services.runner import _issued_queries


def run(coro):
    return asyncio.run(coro)


def _plan() -> dict:
    return {"goal": "find acme", "entity": "company", "requested_count": 5,
            "max_results": 5, "search_queries": ["acme employees"],
            "fields": [{"name": "company_name", "type": "string",
                        "description": "", "required": True}],
            "seed_domains": [], "seed_urls": [], "source_types": [],
            "traversal": {"max_pages_per_domain": 1}, "validation_rules": [],
            "dedupe_keys": ["company_name"], "allowed_sources": [], "max_pages": 2}


async def _emit(_ev):
    pass


def test_discovery_reports_actual_queries_and_the_fatal_reason():
    calls = {"n": 0}

    async def _search(_q, _limit, include=None, exclude=None):
        calls["n"] += 1
        raise AppError("E_PROVIDER_FATAL", "quota exhausted", 424)

    stats: dict = {}
    out = run(discovery.discover("run-stats", _plan(), _search, None, _emit,
                                 stats=stats))
    assert out == []
    # A hard refusal must not be retried: one call, one report, no loop.
    assert calls["n"] == 1
    assert stats["queries"] == 1
    assert "quota exhausted" in stats["fatal"]


def test_issued_queries_prefers_the_report_over_the_projection():
    assert _issued_queries({"queries": 3}, {"queries": 8}) == 3
    assert _issued_queries({"queries": 0}, {"queries": 8}) == 0
    # A test double that reports nothing falls back rather than billing zero.
    assert _issued_queries({}, {"queries": 8}) == 8


# -- the query budget bounds every search path ---------------------------------

def test_diversify_issues_no_more_searches_than_its_allowance():
    """The top-up calls `search_capped` directly, outside the supervisor's
    counter, so a tight credit budget could be exceeded by up to five queries."""
    calls = {"n": 0}

    async def _search(_q, _limit, include=None, exclude=None):
        calls["n"] += 1
        return []

    async def _robots(_url):
        return True

    out = run(discovery._diversify(
        "run-div", _plan(), [{"url": "https://a.example/x"}], 5,
        _search, _robots, _emit, max_extra=1))
    assert calls["n"] == 1
    assert len(out) == 1


def test_a_zero_query_budget_issues_no_search_at_all():
    calls = {"n": 0}

    async def _search(_q, _limit, include=None, exclude=None):
        calls["n"] += 1
        return [{"url": "https://a.example/x", "title": "x", "snippet": ""}]

    stats: dict = {}
    out = run(discovery.discover("run-zero", _plan(), _search, None, _emit,
                                 stats=stats, query_budget=0))
    assert calls["n"] == 0
    assert stats["queries"] == 0
    assert stats["query_budget"] == 0
    assert out == []


def test_a_query_budget_caps_the_top_up_too():
    calls = {"n": 0}

    async def _search(_q, _limit, include=None, exclude=None):
        calls["n"] += 1
        return [{"url": f"https://h{calls['n']}.example/x", "title": "x",
                 "snippet": ""}]

    stats: dict = {}
    run(discovery.discover("run-cap", _plan(), _search, None, _emit,
                           stats=stats, query_budget=1))
    assert calls["n"] <= 1
    assert stats["query_budget"] == 1
