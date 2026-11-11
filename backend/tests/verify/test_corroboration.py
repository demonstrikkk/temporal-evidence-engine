"""Corroboration, source reuse, and the measured cost of a replay.

The acceptance criterion for the cache layer is not "a cache class exists"; it
is that repeating a verification makes fewer external calls. These tests count
the calls instead of trusting the implementation.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.research.source_independence import (
    assess_independence,
    fingerprint_content,
)
from app.research.source_quality import score_evidence, summarise_quality
from app.research.tavily import TavilyClient
from app.verify.orchestrator import Verifier, create_run
from app.verify.schemas import AnswerIn

ANSWER = "Acme Pro costs $99 per month, billed monthly."
LATER = "Effective 2026-10-05, Acme Pro costs $129 per month."


def _start(store, generated_at):
    return create_run(store, AnswerIn(
        answer=ANSWER, prompt="What does Acme Pro cost?",
        generated_at=generated_at, generated_at_source="user_supplied"))


def test_identical_text_is_one_origin_not_two(store):
    page = ("Acme Corporation announced today that Acme Pro costs $99 per "
            "month. For immediate release. Media contact: press@acme.test.")
    source = store.add_source({"id": "s1", "canonical_url": "https://acme.test/a",
                               "domain": "acme.test", "title": "Acme"})
    other = store.add_source({"id": "s2", "canonical_url": "https://wire.test/b",
                              "domain": "wire.test", "title": "Wire"})
    r1 = store.add_revision({"id": "r1", "source_id": source["id"],
                             "content_hash": "h1", "content": page})
    r2 = store.add_revision({"id": "r2", "source_id": other["id"],
                             "content_hash": "h2", "content": page})
    summary = assess_independence(store, [
        {"source_revision_id": r1["id"], "source_url": "https://acme.test/a",
         "publisher": "acme.test"},
        {"source_revision_id": r2["id"], "source_url": "https://wire.test/b",
         "publisher": "wire.test"},
    ])
    assert summary["source_count"] == 2
    assert summary["independent_origins"] == 1
    assert summary["syndicated_groups"] == 1
    assert summary["groups"][0]["copies"] == 2
    assert summary["groups"][0]["same_press_release"] is True


def test_distinct_text_is_two_independent_origins(store):
    source = store.add_source({"id": "s1", "canonical_url": "https://acme.test/a",
                               "domain": "acme.test", "title": "Acme"})
    other = store.add_source({"id": "s2", "canonical_url": "https://news.test/b",
                              "domain": "news.test", "title": "News"})
    r1 = store.add_revision({"id": "r1", "source_id": source["id"],
                             "content_hash": "h1", "content": "Acme Pro is $99."})
    r2 = store.add_revision({"id": "r2", "source_id": other["id"],
                             "content_hash": "h2", "content": "Acme raised prices."})
    summary = assess_independence(store, [
        {"source_revision_id": r1["id"], "source_url": "https://acme.test/a"},
        {"source_revision_id": r2["id"], "source_url": "https://news.test/b"},
    ])
    assert summary["independent_origins"] == 2
    assert summary["syndicated_groups"] == 0


def test_fingerprint_ignores_boilerplate_and_whitespace():
    left = "Acme  Pro costs $99.\n\nFor immediate release. Media contact: x@y.z"
    right = "Acme Pro costs $99. Press release. Media contact: x@y.z"
    assert fingerprint_content(left) == fingerprint_content(right)


def test_official_and_first_party_domains_score_higher(store):
    official = store.add_source({"id": "s1", "canonical_url": "https://openai.com/pricing",
                                 "domain": "openai.com", "title": "OpenAI pricing"})
    r1 = store.add_revision({"id": "r1", "source_id": official["id"],
                             "content_hash": "h1", "content": "GPT pricing.",
                             "published_date": "2026-10-01"})
    blog = store.add_source({"id": "s2", "canonical_url": "http://randomblog.test/post",
                             "domain": "randomblog.test", "title": ""})
    r2 = store.add_revision({"id": "r2", "source_id": blog["id"],
                             "content_hash": "h2", "content": "A blog post."})
    scored = score_evidence(store, "OpenAI", [
        {"source_revision_id": r1["id"], "source_url": "https://openai.com/pricing",
         "publisher": "openai.com", "source_title": "OpenAI pricing",
         "published_date": "2026-10-01"},
        {"source_revision_id": r2["id"], "source_url": "http://randomblog.test/post",
         "publisher": "randomblog.test", "source_title": ""},
    ])
    by_revision = {row.revision_id: row for row in scored}
    assert by_revision["r1"].authority_score > by_revision["r2"].authority_score
    assert "official_domain" in by_revision["r1"].signals
    assert by_revision["r2"].authority_tier == "low"
    assert summarise_quality(scored)["official_sources"] == 1


def test_a_second_run_reuses_evidence_without_searching_again(
        store, make_gateway, make_tavily, claim_payload):
    first = _start(store, datetime(2026, 10, 1, 9, tzinfo=timezone.utc))
    gw1 = make_gateway(
        claims=[claim_payload()],
        responses={"https://acme.test/pricing": {
            "relationship": "supports",
            "quote": "Acme Pro costs $99 per month",
            "candidate_value": "", "reasoning": "same price"}})
    tv1 = make_tavily(pages={
        "https://acme.test/pricing":
            "Acme pricing. Acme Pro costs $99 per month, billed monthly."})
    Verifier(store, first.run_id, bifrost=gw1, tavily=tv1).execute()

    original = store.list_claims(first.run_id)[0]
    assert original["status"] == "supported"
    original_evidence = store.list_evidence(original["id"])

    second = _start(store, datetime(2026, 10, 2, 9, tzinfo=timezone.utc))
    gw2 = make_gateway(claims=[claim_payload()])
    tv2 = make_tavily(pages={})
    Verifier(store, second.run_id, bifrost=gw2, tavily=tv2).execute()

    reused_claim = store.list_claims(second.run_id)[0]
    assert reused_claim["status"] == "supported"
    assert tv2.searches == [], "reuse must not spend a search credit"
    assert gw2.calls == ["extract_claims"], \
        "reuse must not ask a model to re-propose a relationship"
    rows = store.list_evidence(reused_claim["id"])
    assert len(rows) == len(original_evidence)
    assert rows[0]["quote_text"] == original_evidence[0]["quote_text"]
    assert rows[0]["start_char"] == original_evidence[0]["start_char"]
    assert rows[0]["link"]["settlement_status"] == "accepted"
    events = [e["event_type"] for e in store.list_events(second.run_id)]
    assert "claim.evidence_reused" in events


def test_reused_evidence_gets_a_fresh_verdict_for_the_new_generation_time(
        store, make_gateway, make_tavily, claim_payload):
    """Reuse evidence; never reuse a verdict."""
    first = _start(store, datetime(2026, 10, 1, 9, tzinfo=timezone.utc))
    gw = make_gateway(
        claims=[claim_payload()],
        responses={
            "https://acme.test/history": {
                "relationship": "supports",
                "quote": "Effective 2026-09-01, Acme Pro costs $99 per month",
                "candidate_value": "", "reasoning": "the old price"},
            "https://acme.test/pricing": {
                "relationship": "contradicts",
                "quote": LATER, "candidate_value": "$129/month",
                "reasoning": "a new price"}})
    tv = make_tavily(pages={
        "https://acme.test/history":
            "Acme price history. Effective 2026-09-01, Acme Pro costs $99 per "
            "month.",
        "https://acme.test/pricing":
            "Acme pricing. Effective 2026-10-05, Acme Pro costs $129 per month."})
    Verifier(store, first.run_id, bifrost=gw, tavily=tv).execute()
    assert store.list_claims(first.run_id)[0]["status"] == "changed"

    second = _start(store, datetime(2026, 10, 10, 9, tzinfo=timezone.utc))
    tv2 = make_tavily(pages={})
    Verifier(store, second.run_id, bifrost=make_gateway(claims=[claim_payload()]),
             tavily=tv2).execute()

    later_claim = store.list_claims(second.run_id)[0]
    # The same evidence, a later answer: the change is no longer *after*
    # generation, so `changed` would be a lie. The verdict is recomputed.
    assert later_claim["status"] == "contradicted", later_claim["reason_code"]
    assert tv2.searches == []


def test_existing_sources_are_settled_before_search(store, make_gateway,
                                                    make_tavily, claim_payload):
    first = _start(store, datetime(2026, 10, 1, 9, tzinfo=timezone.utc))
    gw = make_gateway(
        claims=[claim_payload()],
        responses={"https://acme.test/pricing": {
            "relationship": "supports",
            "quote": "Acme Pro costs $99 per month",
            "candidate_value": "", "reasoning": "same price"}})
    tv = make_tavily(pages={
        "https://acme.test/pricing":
            "Acme pricing. Acme Pro costs $99 per month, billed monthly."})
    Verifier(store, first.run_id, bifrost=gw, tavily=tv).execute()
    assert tv.searches, "the first run must research"

    # A different answer wording gives a different claim ordinal, but the same
    # stored page can still answer it. Remove the L5 shortcut so this exercises
    # the existing-source rung rather than the claim cache.
    store.purge_cache_by_prefix("l5:claim:")
    second = _start(store, datetime(2026, 10, 2, 9, tzinfo=timezone.utc))
    tv2 = make_tavily(pages={})
    gw2 = make_gateway(
        claims=[claim_payload()],
        responses={"https://acme.test/pricing": {
            "relationship": "supports",
            "quote": "Acme Pro costs $99 per month",
            "candidate_value": "", "reasoning": "same price"}})
    Verifier(store, second.run_id, bifrost=gw2, tavily=tv2).execute()

    claim = store.list_claims(second.run_id)[0]
    assert claim["status"] == "supported"
    assert tv2.searches == [], "stored sources must be settled before searching"
    assert store.list_evidence(claim["id"])


def test_corroboration_is_reported_on_the_settled_claim(store, make_gateway,
                                                        make_tavily, claim_payload):
    run = _start(store, datetime(2026, 10, 1, 9, tzinfo=timezone.utc))
    gw = make_gateway(
        claims=[claim_payload()],
        responses={"https://acme.test/pricing": {
            "relationship": "supports",
            "quote": "Acme Pro costs $99 per month",
            "candidate_value": "", "reasoning": "same price"}})
    tv = make_tavily(pages={
        "https://acme.test/pricing":
            "Acme pricing. Acme Pro costs $99 per month, billed monthly."})
    Verifier(store, run.run_id, bifrost=gw, tavily=tv).execute()

    events = [e for e in store.list_events(run.run_id)
              if e["event_type"] == "claim.corroboration"]
    assert events, "a supported claim must report how independent its sources are"
    payload = events[0]["payload"]
    assert payload["independence"]["independent_origins"] == 1
    assert payload["quality"]["scored_sources"] == 1


class _CountingTavily(TavilyClient):
    """Real caching and extraction logic; the network call is counted."""

    def __init__(self, store, *, search_payload, extract_payload,
                 cache_mode="read-write"):
        super().__init__(store, api_key="test-key", cache_mode=cache_mode)
        self._search_payload = search_payload
        self._extract_payload = extract_payload
        self.calls: list[str] = []

    def _post(self, tool, payload):
        cached = self._cached(tool, payload)
        if cached is not None:
            return cached, True
        if self.cache_mode == "read-only":
            from app.research.tavily import ResearchError
            raise ResearchError(
                "cache_miss",
                f"CACHE_MODE=read-only but no recorded {tool} call.")
        self.calls.append(tool)
        data = (self._search_payload if tool == "search"
                else self._extract_payload)
        response = {**data, "_fetched_at": "2026-10-01T09:00:00+00:00"}
        self._store(tool, payload, response)
        return response, False


def _search_payload():
    return {
        "request_id": "req-1",
        "results": [{"title": "Acme pricing",
                     "url": "https://acme.test/pricing",
                     "content": "snippet", "score": 0.9,
                     "published_date": "2026-09-30"}],
        "failed_results": [],
        "usage": {"credits": 1},
    }


def _extract_payload():
    return {
        "request_id": "req-2",
        "results": [{"url": "https://acme.test/pricing",
                     "raw_content": "Acme pricing. Acme Pro costs $99 per "
                                    "month, billed monthly."}],
        "failed_results": [],
        "usage": {"credits": 1},
    }


def test_repeated_tavily_calls_hit_the_cache_and_report_snapshot(store):
    tavily = _CountingTavily(store, search_payload=_search_payload(),
                             extract_payload=_extract_payload())
    first = tavily.search("Acme Pro price")
    second = tavily.search("Acme Pro price")
    assert first.status == "live"
    assert second.status == "snapshot", "a replay must not masquerade as live"
    assert tavily.calls == ["search"], "the second search must not hit the network"

    extracted = tavily.extract(["https://acme.test/pricing"])
    replayed = tavily.extract(["https://acme.test/pricing"])
    assert extracted.status == "live"
    assert replayed.status == "snapshot"
    assert tavily.calls == ["search", "extract"]
    assert replayed.pages["https://acme.test/pricing"].startswith("Acme pricing")


def test_read_only_mode_refuses_to_invent_research(store):
    tavily = _CountingTavily(store, search_payload=_search_payload(),
                             extract_payload=_extract_payload(),
                             cache_mode="read-only")
    from app.research.tavily import ResearchError

    try:
        tavily.search("Acme Pro price")
    except ResearchError as exc:
        assert exc.kind == "cache_miss"
    else:  # pragma: no cover - the branch itself is the regression
        raise AssertionError("read-only mode must not call the network")
    assert tavily.calls == []
