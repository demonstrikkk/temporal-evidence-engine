"""Degradation that is reported instead of fabricated.

Every test here answers one question: when the outside world is unavailable,
does the product say so, or does it invent a verdict? Quota exhaustion, offline
mode, a duplicate launch, stale stored text and a hallucinated citation are all
ways to get a confident answer with no evidence behind it.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from app.cache.coordinator import CacheCoordinator
from app.cache.keys import claim_fingerprint
from app.research.planner import plan_queries
from app.synthesis.citation_validator import validate_citations
from app.verify.orchestrator import Verifier, create_run
from app.verify.schemas import AnswerIn, ClaimDraft

GEN = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
ANSWER = "Acme Pro costs $99 per month, billed monthly."
PAGE_OLD = "Acme pricing. Acme Pro costs $99 per month, billed monthly."
PAGE_NEW = ("Acme pricing update. Effective 2026-10-05, Acme Pro costs "
            "$129 per month.")


def _start(store, *, generated_at=GEN, answer=ANSWER, budget_mode=None):
    kwargs = dict(answer=answer, prompt="What does Acme Pro cost?",
                  generated_at=generated_at,
                  generated_at_source="user_supplied")
    if budget_mode is not None:
        kwargs["budget_mode"] = budget_mode
    return create_run(store, AnswerIn(**kwargs))


def _supporting_gateway(make_gateway, claim_payload):
    return make_gateway(
        claims=[claim_payload()],
        responses={"https://acme.test/pricing": {
            "relationship": "supports",
            "quote": "Acme Pro costs $99 per month",
            "candidate_value": "", "reasoning": "same price"}})


# ---------------------------------------------------------------------------
# budget modes
# ---------------------------------------------------------------------------

def test_offline_mode_abstains_instead_of_lowering_the_evidence_bar(
        store, make_gateway, make_tavily, claim_payload):
    run = _start(store, budget_mode="offline")
    row = store.get_run(run.run_id)
    assert row["budget_mode"] == "offline"

    tv = make_tavily(pages={"https://acme.test/pricing": PAGE_OLD})
    Verifier(store, run.run_id, bifrost=make_gateway(claims=[claim_payload()]),
             tavily=tv).execute()

    claim = store.list_claims(run.run_id)[0]
    assert tv.searches == [], "offline mode must spend zero search credits"
    assert claim["status"] == "unverifiable", \
        "with no evidence the only honest verdict is unverifiable"
    assert claim["reason_code"] == "no_source"
    assert store.list_evidence(claim["id"]) == []


def test_offline_mode_still_settles_sources_it_already_holds(
        store, make_gateway, make_tavily, claim_payload):
    """Offline means no network, not no verification."""
    first = _start(store)
    Verifier(store, first.run_id,
             bifrost=_supporting_gateway(make_gateway, claim_payload),
             tavily=make_tavily(pages={"https://acme.test/pricing": PAGE_OLD})
             ).execute()
    assert store.list_claims(first.run_id)[0]["status"] == "supported"

    # A different wording forces the existing-source rung rather than L5.
    store.purge_cache_by_prefix("l5:claim:")
    second = _start(store, budget_mode="offline")
    tv2 = make_tavily(pages={})
    Verifier(store, second.run_id,
             bifrost=_supporting_gateway(make_gateway, claim_payload),
             tavily=tv2).execute()

    claim = store.list_claims(second.run_id)[0]
    assert claim["status"] == "supported"
    assert tv2.searches == []


def test_the_mode_a_run_was_created_under_is_stored_on_the_run(
        store, monkeypatch):
    from app.governance.resource_budget import budget_manager
    monkeypatch.setattr(budget_manager, "_mode", "conserve")
    run = _start(store)
    assert store.get_run(run.run_id)["budget_mode"] == "conserve"

    explicit = _start(store, budget_mode="deep")
    assert store.get_run(explicit.run_id)["budget_mode"] == "deep", \
        "an explicit per-request mode must win over the global default"


def test_each_mode_buys_a_different_quantity_of_research():
    claim = ClaimDraft(claim_text="Acme Pro costs $99 per month",
                       subject="Acme Pro", predicate="price",
                       object_raw="$99/month")
    assert plan_queries(claim, budget_mode="offline") == []
    conserve = plan_queries(claim, budget_mode="conserve")
    balanced = plan_queries(claim, budget_mode="balanced")
    deep = plan_queries(claim, budget_mode="deep")
    assert len(conserve) == 1
    assert len(balanced) >= len(conserve)
    assert len(deep) >= len(balanced)
    assert len(deep) > len(conserve), \
        "deep must buy more research than conserve, or the mode is decoration"


# ---------------------------------------------------------------------------
# exhaustion and failure
# ---------------------------------------------------------------------------

def test_quota_exhaustion_produces_abstention_not_an_answer(
        store, make_gateway, make_tavily, claim_payload):
    from app.research.tavily import ResearchError

    class _Exhausted:
        searches: list[str] = []

        def search(self, query, **_):
            self.searches.append(query)
            raise ResearchError("fatal", "Tavily monthly credits exhausted")

        def extract(self, urls, **_):  # pragma: no cover - never reached
            raise ResearchError("fatal", "Tavily monthly credits exhausted")

    run = _start(store)
    tv = _Exhausted()
    Verifier(store, run.run_id,
             bifrost=make_gateway(claims=[claim_payload()]), tavily=tv).execute()

    claim = store.list_claims(run.run_id)[0]
    assert claim["status"] == "unverifiable"
    assert claim["reason_code"] == "no_source"
    assert store.list_evidence(claim["id"]) == [], \
        "an exhausted quota must never be papered over with invented evidence"
    assert store.get_run(run.run_id)["status"] == "partial"
    kinds = [e["event_type"] for e in store.list_events(run.run_id)]
    assert "search.failed" in kinds
    assert "run.partial" in kinds


def test_launching_the_same_run_twice_executes_it_once(monkeypatch):
    from app.verify import orchestrator

    started = threading.Event()
    release = threading.Event()
    executions: list[str] = []

    class _StubVerifier:
        def __init__(self, store, run_id):
            self.run_id = run_id

        def execute(self):
            executions.append(self.run_id)
            started.set()
            # Still "running" while the second launch is attempted.
            release.wait(2.0)

    monkeypatch.setattr(orchestrator, "Verifier", _StubVerifier)
    orchestrator.launch(store=None, run_id="run-duplicate")
    assert started.wait(2.0), "the worker thread never ran"
    orchestrator.launch(store=None, run_id="run-duplicate")
    release.set()
    for _ in range(200):
        with orchestrator._LOCK:
            if "run-duplicate" not in orchestrator._ACTIVE_RUNS:
                break
        threading.Event().wait(0.01)
    assert executions == ["run-duplicate"], \
        "a duplicate launch must not start a second verification of the same run"


# ---------------------------------------------------------------------------
# stale evidence
# ---------------------------------------------------------------------------

def test_new_content_for_a_url_invalidates_the_evidence_cached_from_the_old_text(
        store, make_gateway, make_tavily, claim_payload):
    first = _start(store)
    Verifier(store, first.run_id,
             bifrost=_supporting_gateway(make_gateway, claim_payload),
             tavily=make_tavily(pages={"https://acme.test/pricing": PAGE_OLD})
             ).execute()
    assert store.list_claims(first.run_id)[0]["status"] == "supported"

    old_key = claim_fingerprint("Acme Pro", "price", None, "$99/month")
    assert store.get_cache_entry(old_key) is not None, \
        "the first run should have cached its settled evidence"

    # A different claim cannot use L5 (different value scope) and cannot be
    # settled by the stored page, so it escalates to search and discovers the
    # page has changed. That discovery must evict the old cached evidence.
    second = _start(store, answer="Acme Pro is available in Germany.",
                    generated_at=datetime(2026, 10, 2, 9, tzinfo=timezone.utc))
    other_claim = claim_payload(claim_text="Acme Pro is available in Germany",
                                subject="Acme Pro", predicate="availability",
                                object_raw="Germany", claim_type="factual",
                                time_sensitive=False, volatility_class="low")
    tv2 = make_tavily(pages={"https://acme.test/pricing": PAGE_NEW})
    Verifier(store, second.run_id, bifrost=make_gateway(claims=[other_claim]),
             tavily=tv2).execute()

    assert tv2.searches, "the second claim had to be researched live"
    assert store.get_cache_entry(old_key) is None, \
        "evidence settled from the old text must not be reused after the page changed"
    revisions = [r for r in store.list_revisions_for_source(
        store.list_sources(limit=1)[0]["id"], limit=10)]
    hashes = {r["content_hash"] for r in revisions}
    assert len(hashes) == 2, "the changed page must be stored as a new revision"


def test_invalidation_targets_dependent_entries_only(store):
    coord = CacheCoordinator(store)
    source = store.add_source({"id": "s1",
                               "canonical_url": "https://acme.test/pricing",
                               "domain": "acme.test", "title": "Acme"})
    other = store.add_source({"id": "s2",
                              "canonical_url": "https://other.test/page",
                              "domain": "other.test", "title": "Other"})
    for revision_id, source_id, content in (
            ("r1", source["id"], "Acme Pro costs $99 per month."),
            ("r2", other["id"], "Something else entirely.")):
        store.add_revision({"id": revision_id, "source_id": source_id,
                            "content_hash": f"h-{revision_id}",
                            "content": content})
        store.add_evidence({"id": f"e-{revision_id}", "claim_id": f"c-{revision_id}",
                            "source_revision_id": revision_id,
                            "quote_text": content[:20], "start_char": 0,
                            "end_char": 20, "event_date_status": "missing"})

    coord.put_l5("Acme Pro", "price",
                 [{"evidence_id": "e-r1", "settled": True,
                   "relationship": "supports"}], value_scope="$99/month")
    coord.put_l5("Other", "purpose",
                 [{"evidence_id": "e-r2", "settled": True,
                   "relationship": "supports"}], value_scope="testing")

    acme_key = claim_fingerprint("Acme Pro", "price", None, "$99/month")
    other_key = claim_fingerprint("Other", "purpose", None, "testing")
    assert store.get_cache_entry(acme_key) and store.get_cache_entry(other_key)

    removed = coord.invalidate_for_source_revision(
        "https://acme.test/pricing", "h-new")
    assert removed == 1
    assert store.get_cache_entry(acme_key) is None
    assert store.get_cache_entry(other_key) is not None, \
        "an unrelated claim's cached evidence must survive"


# ---------------------------------------------------------------------------
# answer validation
# ---------------------------------------------------------------------------

def _context():
    return {
        "sources": [{"evidence_id": "e_1", "quote_text": "Acme Pro costs $99"}],
        "verified_claims": [{
            "text": "Acme Pro costs $99 per month",
            "value_raw": "$99/month",
            "evidence_ids": ["e_1"],
            "subject": "Acme Pro", "predicate": "price",
        }],
        "unverifiable_claims": [],
    }


def test_a_number_that_was_not_verified_is_rejected():
    result = validate_citations(
        "Acme Pro costs $109 per month [e_1].", _context())
    assert any("$99" in error for error in result.errors)
    assert result.is_valid is False


def test_an_invented_citation_is_rejected():
    result = validate_citations(
        "Acme Pro costs $99 per month [e_999].", _context())
    assert result.is_valid is False
    assert result.quality_metrics["invalid_citations"] == ["e_999"]


def test_an_uncited_factual_sentence_is_reported():
    result = validate_citations(
        "Acme Pro costs $99 per month [e_1]. Acme raised prices in 2026.",
        _context())
    assert result.unsupported_assertions
    assert any("2026" in line for line in result.unsupported_assertions)
