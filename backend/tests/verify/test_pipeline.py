"""End-to-end over the real orchestrator, the real gate and the real decision table.

Only the two external services are fakes. Everything between them — storage,
quote location, settlement, event dates, precedence — is production code.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.verify.orchestrator import Verifier, create_run
from app.verify.schemas import AnswerIn

ANSWER = ("Acme Pro costs $99 per month, billed monthly. Acme was founded in "
          "2015 and is headquartered in Berlin.")
GEN = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def _start(store, *, generated_at=GEN, answer=ANSWER, prompt="What does Acme Pro cost?"):
    payload = AnswerIn(answer=answer, prompt=prompt, generated_at=generated_at,
                       generated_at_source="user_supplied")
    return create_run(store, payload)


def _events(store, run_id, kind):
    return [e for e in store.list_events(run_id) if e["event_type"] == kind]


def test_a_locatable_support_is_supported(store, make_gateway, make_tavily,
                                          claim_payload):
    run = _start(store)
    gw = make_gateway(
        claims=[claim_payload()],
        responses={"https://acme.test/pricing": {
            "relationship": "supports",
            "quote": "Acme Pro costs $99 per month",
            "candidate_value": "", "reasoning": "same price"}})
    tv = make_tavily(pages={
        "https://acme.test/pricing":
            "Acme pricing\n\nAcme Pro costs $99 per month, billed monthly. "
            "Enterprise is $499 per month."})

    Verifier(store, run.run_id, bifrost=gw, tavily=tv).execute()

    claims = store.list_claims(run.run_id)
    assert [c["status"] for c in claims] == ["supported"]
    assert claims[0]["reason_code"] == "supported_by_source"
    evidence = store.list_evidence(claims[0]["id"])
    assert len(evidence) == 1
    row = evidence[0]
    content = row["revision"]["content"]
    assert content[row["start_char"]:row["end_char"]] == row["quote_text"]
    assert row["link"]["settlement_status"] == "accepted"
    assert row["link"]["relationship_settled"] == "supports"
    assert row["start_char"] > 0  # absolute offset into the stored text
    assert store.get_run(run.run_id)["status"] == "completed"


def test_support_plus_a_later_settled_replacement_is_changed(
        store, make_gateway, make_tavily, claim_payload):
    run = _start(store)
    gw = make_gateway(
        claims=[claim_payload()],
        responses={
            "https://acme.test/history": {
                "relationship": "supports",
                "quote": "Effective 2026-09-01, Acme Pro costs $99 per month",
                "candidate_value": "", "reasoning": "the old price"},
            "https://acme.test/pricing": {
                "relationship": "contradicts",
                "quote": "Effective 2026-10-05, Acme Pro costs $129 per month",
                "candidate_value": "$129/month", "reasoning": "a new price"},
        })
    tv = make_tavily(pages={
        "https://acme.test/history":
            "Acme price history. Effective 2026-09-01, Acme Pro costs $99 per "
            "month. This was announced in August.",
        "https://acme.test/pricing":
            "Acme pricing. Effective 2026-10-05, Acme Pro costs $129 per month.",
    })

    Verifier(store, run.run_id, bifrost=gw, tavily=tv).execute()

    claim = store.list_claims(run.run_id)[0]
    assert claim["status"] == "changed", claim["reason_code"]
    assert claim["reason_code"] == "value_changed_after_generation"
    settled = _events(store, run.run_id, "claim.settled")[-1]["payload"]
    assert settled["rule"] == "R5"
    assert settled["replacement_evidence_id"]
    assertions = store.list_assertions(claim["id"])
    assert len(assertions) == 2
    assert assertions[1]["value_raw"] == "$129/month"
    assert assertions[1]["valid_from"] == "2026-10-05"


def test_a_contradiction_without_an_event_date_cannot_be_a_change(
        store, make_gateway, make_tavily, claim_payload):
    run = _start(store)
    gw = make_gateway(
        claims=[claim_payload()],
        responses={
            "https://acme.test/history": {
                "relationship": "supports",
                "quote": "Effective 2026-09-01, Acme Pro costs $99 per month",
                "candidate_value": "", "reasoning": "the old price"},
            "https://acme.test/news": {
                "relationship": "contradicts",
                "quote": "Acme Pro now costs $129 per month",
                "candidate_value": "$129/month", "reasoning": "a new price"},
        })
    tv = make_tavily(pages={
        "https://acme.test/history":
            "Acme price history. Effective 2026-09-01, Acme Pro costs $99 per month.",
        "https://acme.test/news":
            "Acme news. Acme Pro now costs $129 per month.",
    })

    Verifier(store, run.run_id, bifrost=gw, tavily=tv).execute()

    claim = store.list_claims(run.run_id)[0]
    assert claim["status"] != "changed"
    assert claim["reason_code"] == "temporal_ambiguity"


def test_no_evidence_at_all_is_unverifiable_not_false(store, make_gateway,
                                                     make_tavily, claim_payload):
    run = _start(store)
    gw = make_gateway(claims=[claim_payload()], responses={})
    tv = make_tavily(pages={})

    Verifier(store, run.run_id, bifrost=gw, tavily=tv).execute()

    claim = store.list_claims(run.run_id)[0]
    assert claim["status"] == "unverifiable"
    assert claim["reason_code"] == "no_source"
    assert store.list_evidence(claim["id"]) == []


def test_a_quote_that_does_not_resolve_is_discarded(store, make_gateway,
                                                    make_tavily, claim_payload):
    run = _start(store)
    gw = make_gateway(
        claims=[claim_payload()],
        responses={"https://acme.test/pricing": {
            "relationship": "supports",
            "quote": "Acme Pro costs $99 per month but this sentence is invented",
            "candidate_value": "", "reasoning": "x"}})
    tv = make_tavily(pages={
        "https://acme.test/pricing": "Acme Pro costs $99 per month."})

    Verifier(store, run.run_id, bifrost=gw, tavily=tv).execute()

    claim = store.list_claims(run.run_id)[0]
    assert claim["status"] == "unverifiable"
    skipped = _events(store, run.run_id, "evidence.skipped")
    assert skipped and skipped[0]["payload"]["reason"] == "quote_not_locatable"
    assert store.list_evidence(claim["id"]) == []


def test_a_replayed_extraction_is_labelled_in_the_event_rail(
        store, make_gateway, make_tavily, claim_payload):
    """A snapshot run spent nothing, so it must not look like a live one."""
    run = _start(store)

    Verifier(store, run.run_id,
             bifrost=make_gateway(claims=[claim_payload()], status="snapshot"),
             tavily=make_tavily()).execute()

    events = store.list_events(run.run_id)
    snapshots = [e for e in events if e["event_type"] == "model.snapshot"]
    assert snapshots, "a replayed model call must say so"
    assert snapshots[0]["payload"]["stage"] == "extract_claims"
    assert snapshots[0]["payload"]["created_at"], \
        "a replay must say when the answer it replayed was recorded"
    assert events[-1]["payload"]["counters"]["replays"] == 1


def test_a_replayed_proposal_carries_the_time_it_was_recorded(
        store, make_gateway, make_tavily, claim_payload):
    """The rail must distinguish a replayed answer from an undated one."""
    run = _start(store)
    gw = make_gateway(
        claims=[claim_payload()], status="snapshot",
        responses={"https://acme.test/pricing": {
            "relationship": "supports",
            "quote": "Acme Pro costs $99 per month",
            "candidate_value": "", "reasoning": ""}})
    tv = make_tavily(pages={
        "https://acme.test/pricing":
            "Acme pricing. Acme Pro costs $99 per month, billed monthly."})

    Verifier(store, run.run_id, bifrost=gw, tavily=tv).execute()

    events = store.list_events(run.run_id)
    proposals = [e["payload"] for e in events
                 if e["event_type"] == "model.snapshot"
                 and e["payload"].get("stage") == "propose_relationship"]
    assert proposals, "the proposal replay must appear in the rail"
    assert all(row.get("created_at") for row in proposals), proposals
    assert all(row.get("provider") and row.get("model") for row in proposals), proposals


def test_a_passage_about_another_plan_does_not_refute_this_one(
        store, make_gateway, make_tavily, claim_payload):
    """One model mistake used to outweigh four agreeing sources.

    Live, `Business costs $24 monthly` settled as a contradiction of `Notion
    Plus costs $10 per member per month`: the value gate saw $24 in the quote and
    never asked which plan the quote was about. The claim is supported by its own
    sources, and the mislabelled passage is recorded as unsettled evidence
    rather than silently dropped.
    """
    run = _start(store)
    gw = make_gateway(
        claims=[claim_payload()],
        responses={
            "https://acme.test/pricing": {
                "relationship": "supports",
                "quote": "Acme Pro costs $99 per month",
                "candidate_value": "", "reasoning": "same price"},
            "https://acme.test/plans": {
                "relationship": "contradicts",
                "quote": "Acme Business costs $249 per month",
                "candidate_value": "$249/month",
                "reasoning": "a different plan's price"},
        })
    tv = make_tavily(pages={
        "https://acme.test/pricing":
            "Acme pricing. Acme Pro costs $99 per month, billed monthly.",
        "https://acme.test/plans":
            "Acme plans. Acme Business costs $249 per month.",
    })

    Verifier(store, run.run_id, bifrost=gw, tavily=tv).execute()

    claim = store.list_claims(run.run_id)[0]
    assert claim["status"] == "supported", claim["reason_code"]
    rows = store.list_evidence(claim["id"])
    settled = {row["link"]["relationship_proposed"]: row["link"]["settlement_status"]
               for row in rows}
    assert settled == {"supports": "accepted", "contradicts": "rejected"}
    # A rejected proposal has no settled relationship. Writing a settlement word
    # into `relationship_settled` made the column unable to answer "which
    # relationship survived?", which is the only question its name asks.
    rejected = [row for row in rows if row["link"]["settlement_status"] == "rejected"]
    assert rejected and all(row["link"]["relationship_settled"] is None
                            for row in rejected)
    accepted = [row for row in rows if row["link"]["settlement_status"] == "accepted"]
    assert accepted and all(row["link"]["relationship_settled"] == "supports"
                            for row in accepted)
    skipped = _events(store, run.run_id, "evidence.settled")
    refusal = [e for e in skipped if not e["payload"]["settled"]][0]
    assert "subject" in refusal["payload"]["reason"], refusal["payload"]["reason"]


def test_first_party_credibility_is_read_from_the_domain_label():
    """R7 needs to know which sources are first-party. It never could before.

    `_credibility` stripped every non-alphanumeric character from the host
    before splitting on the dot, so `acme.example` became `acmeexample`, the
    `"." in host` branch was unreachable, and no subject ever matched its own
    domain. The rule existed, had a direct unit test, and could not fire.
    """
    from app.verify.orchestrator import _credibility

    assert _credibility("acme.example", "Acme Pro") == "primary"
    assert _credibility("www.acme.com", "Acme") == "primary"
    assert _credibility("techcrunch.com", "Acme Pro") == "secondary"
    assert _credibility("notacme.example", "Acme Pro") == "secondary"
    assert _credibility("", "Acme Pro") == "secondary"


def test_a_model_outage_ends_the_run_as_failed_not_as_a_verdict(store, make_gateway,
                                                                make_tavily):
    run = _start(store)
    gw = make_gateway(claims=[], fail_stage="extract_claims")
    tv = make_tavily()

    Verifier(store, run.run_id, bifrost=gw, tavily=tv).execute()

    stored = store.get_run(run.run_id)
    assert stored["status"] == "failed"
    assert stored["error"]["code"] == "extraction_failed"
    assert store.list_claims(run.run_id) == []
    assert _events(store, run.run_id, "model.failed")

    # A hard failure is its own terminal event. Emitting `run.partial` here made
    # "nothing was verified at all" indistinguishable from "one source was lost".
    terminal = store.list_events(run.run_id)[-1]
    assert terminal["event_type"] == "run.failed"
    assert terminal["payload"]["error"]["code"] == "extraction_failed"


def test_a_run_with_nothing_checkable_says_so_in_its_terminal_event(
        store, make_gateway, make_tavily):
    run = _start(store)

    Verifier(store, run.run_id, bifrost=make_gateway(claims=[]),
             tavily=make_tavily()).execute()

    stored = store.get_run(run.run_id)
    assert stored["status"] == "partial"
    assert stored["error"]["code"] == "no_claims"

    # The terminal payload carries the reason. Without it the event rail showed
    # a partial run with zero failures and no explanation.
    terminal = store.list_events(run.run_id)[-1]
    assert terminal["event_type"] == "run.partial"
    assert terminal["payload"]["error"]["code"] == "no_claims"
    assert "no checkable" in terminal["payload"]["error"]["message"]


def test_a_failed_search_is_reported_and_makes_the_run_partial(
        store, make_gateway, make_tavily, claim_payload):
    run = _start(store)
    gw = make_gateway(claims=[claim_payload()])
    tv = make_tavily(fail_search=True)

    Verifier(store, run.run_id, bifrost=gw, tavily=tv).execute()

    assert store.get_run(run.run_id)["status"] == "partial"
    assert _events(store, run.run_id, "search.failed")
    claims = store.list_claims(run.run_id)
    assert claims[0]["status"] == "unverifiable"
    assert claims[0]["reason_code"] == "no_source"


def test_events_are_durable_ordered_and_end_with_the_terminal_event(
        store, make_gateway, make_tavily, claim_payload):
    run = _start(store)
    gw = make_gateway(
        claims=[claim_payload()],
        responses={"https://acme.test/pricing": {
            "relationship": "supports",
            "quote": "Acme Pro costs $99 per month", "candidate_value": "",
            "reasoning": "x"}})
    tv = make_tavily(pages={
        "https://acme.test/pricing": "Acme Pro costs $99 per month."})

    Verifier(store, run.run_id, bifrost=gw, tavily=tv).execute()

    events = store.list_events(run.run_id)
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert events[0]["event_type"] == "run.started"
    assert events[-1]["event_type"] == "run.completed"
    assert _events(store, run.run_id, "source.stored")
    assert _events(store, run.run_id, "evidence.settled")


def test_the_report_is_built_from_stored_rows(store, make_gateway, make_tavily,
                                              claim_payload):
    from app.verify.report import build_report

    run = _start(store)
    gw = make_gateway(
        claims=[claim_payload()],
        responses={"https://acme.test/pricing": {
            "relationship": "supports",
            "quote": "Acme Pro costs $99 per month", "candidate_value": "",
            "reasoning": "x"}})
    tv = make_tavily(pages={
        "https://acme.test/pricing": "Acme Pro costs $99 per month."})
    Verifier(store, run.run_id, bifrost=gw, tavily=tv).execute()

    report = build_report(store, run.run_id)
    assert report["run"]["id"] == run.run_id
    assert report["claims"][0]["status"] == "supported"
    assert "Acme Pro costs $99 per month" in report["markdown"]
    assert "Supported" in report["markdown"]
    json.dumps(report, default=str)  # must be exportable
