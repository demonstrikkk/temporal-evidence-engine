"""WP-3: one research plan per subject group, researched once.

A group of claims that shares a subject issues the deduped union of its
queries once, settles the pages it returns against every claim in the group,
and reports what the pre-filter refused to send. Nothing here stubs `decide`,
`settle` or storage — the gateway and the search engine are the only fakes.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.research.planner import plan_queries_detail
from app.verify.orchestrator import Verifier, create_run
from app.verify.schemas import AnswerIn, ClaimDraft, ClaimStatus
from app.verify.store import LocalStore

PRICING_PAGE = (
    "Acme pricing\n\n"
    "Acme Pro costs $99 per month, billed monthly. "
    "Enterprise is $499 per month."
)

GEN = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
PAGE_URL = "https://acme.test/pricing"
WEATHER_URL = "https://example.test/weather"


def _claim(base=None, **overrides):
    c = dict(
        claim_text="Acme Pro costs $99 per month",
        subject="Acme Pro",
        predicate="price",
        object_raw="$99/month",
        claim_type="time_sensitive",
        time_sensitive=True,
        volatility_class="high",
        extraction_confidence=0.9,
    )
    if base:
        c.update(base)
    c.update(overrides)
    return c


def _start(store, claims, budget_mode="balanced", prompt="What does Acme Pro cost?"):
    return create_run(
        store,
        AnswerIn(
            answer="\n".join(c["claim_text"] for c in claims),
            prompt=prompt,
            generated_at=GEN,
            generated_at_source="user_supplied",
            budget_mode=budget_mode,
        ),
    )


def _spans(store, run_id):
    header = store.get_execution_trace(run_id)
    assert header, "a run with tracing on must have a trace header"
    return store.list_trace_spans(str(header["trace_id"]))


def _run(store, claims, gw, tv, budget_mode="balanced"):
    run = _start(store, claims, budget_mode=budget_mode)
    Verifier(store, run.run_id, bifrost=gw, tavily=tv).execute()
    return run


def _supports_response(quote="Acme Pro costs $99 per month"):
    return {
        "relationship": "supports",
        "quote": quote,
        "candidate_value": "",
        "reasoning": "same price",
    }


def test_five_claims_one_subject_share_searches_and_extract_once(
    store, make_gateway, make_tavily, tmp_path
):
    base = _claim()
    texts = [
        "Acme Pro costs $99 per month.",
        "The price of Acme Pro is $99 per month.",
        "Acme Pro is priced at $99 per month.",
        "You pay $99 per month for Acme Pro.",
        "Acme Pro: $99 per month.",
    ]
    claims = [dict(base, claim_text=t) for t in texts]
    gw = make_gateway(
        claims=claims,
        responses={PAGE_URL: _supports_response()},
    )
    tv = make_tavily(pages={PAGE_URL: PRICING_PAGE})

    run = _run(store, claims, gw, tv)

    # One group, so the deduped union of the queries is asked once each.
    plans = store.list_research_plans(run.run_id)
    assert plans, plans
    planned_queries = plans[0]["queries"]
    assert len(tv.searches) == len(planned_queries), tv.searches
    # The shared page was extracted once for the whole group, not five times.
    assert len(tv.extracts) == 1
    assert tv.extracts[0]["urls"] == [PAGE_URL]

    # The same five claims run solo (fresh store each time, so no source
    # carries over between runs) cost five times as many searches.
    one_claim_queries, _ = plan_queries_detail(ClaimDraft(**claims[0]), "balanced")
    solo_searches = 0
    for i, claim in enumerate(claims):
        solo_store = LocalStore(str(tmp_path / f"solo-{i}"))
        s = make_tavily(pages={PAGE_URL: PRICING_PAGE})
        g = make_gateway(claims=[claim], responses={PAGE_URL: _supports_response()})
        r = create_run(solo_store, AnswerIn(
            answer=claim["claim_text"], prompt="x",
            generated_at=GEN, generated_at_source="user_supplied"))
        Verifier(solo_store, r.run_id, bifrost=g, tavily=s).execute()
        solo_searches += len(s.searches)
    assert solo_searches == 5 * len(one_claim_queries)
    assert len(tv.searches) < solo_searches

    # Identical verdicts for every claim in the group.
    claim_rows = store.list_claims(run.run_id)
    assert [c["status"] for c in claim_rows] == ["supported"] * 5


def test_page_sharing_no_term_is_skipped_without_a_model_call(
    store, make_gateway, make_tavily
):
    claims = [_claim()]
    # The weather page shares no term with "Acme Pro" / "$99/month".
    gw = make_gateway(claims=claims, responses={})
    tv = make_tavily(pages={WEATHER_URL: "Berlin weather today is sunny."})

    run = _run(store, claims, gw, tv)

    prefiltered = [
        e for e in store.list_events(run.run_id)
        if e["event_type"] == "evidence.prefiltered"
    ]
    assert prefiltered, prefiltered
    assert prefiltered[0]["payload"]["url"] == WEATHER_URL
    # No judge call was made for the skipped page.
    spans = _spans(store, run.run_id)
    judge_spans = [s for s in spans if s["operation"] == "judge_evidence"]
    assert not judge_spans, judge_spans


def test_page_sharing_a_term_is_still_examined(
    store, make_gateway, make_tavily
):
    claims = [_claim()]
    gw = make_gateway(
        claims=claims,
        responses={WEATHER_URL: {
            "relationship": "mentions_only", "quote": "",
            "candidate_value": "", "reasoning": "no price here"},
        PAGE_URL: _supports_response(),
    })
    tv = make_tavily(pages={
        WEATHER_URL: "Acme Pro is available in Berlin and Munich.",
        PAGE_URL: PRICING_PAGE,
    })

    run = _run(store, claims, gw, tv)

    # The page sharing "Acme" was sent to the model, not pre-filtered.
    prefiltered = [
        e for e in store.list_events(run.run_id)
        if e["event_type"] == "evidence.prefiltered"
    ]
    assert not any(e["payload"]["url"] == WEATHER_URL for e in prefiltered)
    spans = _spans(store, run.run_id)
    judge_spans = [s for s in spans if s["operation"] == "judge_evidence"]
    assert judge_spans, judge_spans

    claim_rows = store.list_claims(run.run_id)
    assert claim_rows[0]["status"] == ClaimStatus.SUPPORTED.value


def test_claim_with_no_usable_terms_is_never_skipped(
    store, make_gateway, make_tavily
):
    claims = [_claim(subject="", predicate="", object_raw="",
                    claim_text="Something happened in Berlin on Tuesday.")]
    gw = make_gateway(
        claims=claims,
        responses={WEATHER_URL: {
            "relationship": "mentions_only", "quote": "",
            "candidate_value": "", "reasoning": "unrelated"},
    })
    tv = make_tavily(pages={WEATHER_URL: "Berlin weather today is sunny."})

    run = _run(store, claims, gw, tv)

    # Empty subject/object means no usable term, so nothing may be skipped.
    prefiltered = [
        e for e in store.list_events(run.run_id)
        if e["event_type"] == "evidence.prefiltered"
    ]
    assert not prefiltered, prefiltered
    spans = _spans(store, run.run_id)
    judge_spans = [s for s in spans if s["operation"] == "judge_evidence"]
    assert judge_spans, judge_spans


def test_conserve_budget_truncates_and_reports_dropped(
    store, make_gateway, make_tavily
):
    claims = [_claim()]
    gw = make_gateway(
        claims=claims,
        responses={PAGE_URL: _supports_response()},
    )
    tv = make_tavily(pages={PAGE_URL: PRICING_PAGE})

    run = _run(store, claims, gw, tv, budget_mode="conserve")

    planned, _ = plan_queries_detail(ClaimDraft(**claims[0]), "conserve")
    # Conserve allows exactly one query; the rest are refused, not hidden.
    assert len(planned) == 1
    plans = store.list_research_plans(run.run_id)
    assert plans, plans
    plan = plans[0]
    assert len(plan["queries"]) == 1, plan
    assert len(plan["queries_asked"]) == 1, plan
    assert int(plan["queries_dropped"]) > 0

    # The budget exhaustion is an event, not a silent skip.
    events = [e for e in store.list_events(run.run_id)
              if e["event_type"] == "research.budget_exhausted"]
    assert events, events

    # The report prints the planned/asked split and the Dropped column.
    from app.verify.report import build_report
    report = build_report(store, run.run_id)
    markdown = report["markdown"]
    assert "Dropped" in markdown
    assert "Research plan" in markdown
    assert str(int(plan["queries_dropped"])) in markdown
