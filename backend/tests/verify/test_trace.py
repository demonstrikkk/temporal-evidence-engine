"""The run as a tree: one span per operation, one event per span.

WP-2's acceptance criteria, as tests:

* replaying a recorded run reproduces the tree (same spans, same parents, same
  order) - and the test says what "same" leaves out, because timings are
  measurements rather than structure;
* a model failure is a span carrying `error_type`, not a hole in the tree;
* `prompt_version` cannot drift from the prompt it names.

The rest pins the things a cost table gets wrong: a replay priced like a live
call, a bill added to an estimate, a span written without the event that
reports it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.verify import prompts
from app.verify.orchestrator import Verifier, create_run
from app.verify.schemas import AnswerIn
from app.verify.store import LocalStore
from app.verify.trace import build_tree, fingerprint, summarise

GEN = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
ANSWER = "Acme Pro costs $99 per month."
PAGE = ("Acme pricing\n\nAcme Pro costs $99 per month, billed monthly. "
        "Enterprise is $499 per month.")


def _start(store):
    return create_run(store, AnswerIn(
        answer=ANSWER, prompt="What does Acme Pro cost?",
        generated_at=GEN, generated_at_source="user_supplied"))


def _run(store, make_gateway, make_tavily, *, claim_payload, pages=None,
         fail_stage="", status="live"):
    run = _start(store)
    gateway = make_gateway(
        claims=[claim_payload()],
        responses={"https://acme.test/pricing": {
            "relationship": "supports",
            "quote": "Acme Pro costs $99 per month",
            "candidate_value": "", "reasoning": "same price"}},
        fail_stage=fail_stage, status=status)
    tavily = make_tavily(pages=pages if pages is not None else {
        "https://acme.test/pricing": PAGE})
    Verifier(store, run.run_id, bifrost=gateway, tavily=tavily).execute()
    return run, gateway


def _spans(store, run_id):
    header = store.get_execution_trace(run_id)
    assert header, "a run with tracing on must have a trace header"
    return header, store.list_trace_spans(str(header["trace_id"]))


# ---------------------------------------------------------------------------
# the tree exists, and it is the run
# ---------------------------------------------------------------------------

def test_a_run_produces_one_root_and_a_span_for_every_stage(
        store, make_gateway, make_tavily, claim_payload):
    run, _ = _run(store, make_gateway, make_tavily, claim_payload=claim_payload)
    header, spans = _spans(store, run.run_id)

    roots = [row for row in spans if not row.get("parent_span_id")]
    assert len(roots) == 1, "one run is one root, not two"
    assert roots[0]["id"] == header["root_span_id"]
    assert roots[0]["operation"] == "run"
    assert header["status"] == "completed"

    operations = {(row["stage"], row["operation"]) for row in spans}
    assert ("extract", "model_call") in operations
    assert ("coverage", "audit") in operations
    assert ("claim", "verify_claim") in operations
    assert ("research", "search") in operations
    assert ("research", "extract") in operations
    assert ("research", "judge_evidence") in operations

    tree = build_tree(spans)
    assert tree["id"] == header["root_span_id"]
    assert tree["children"], "the root must have the run's stages beneath it"
    # A child's parent is a span in the same trace, never a dangling id.
    ids = {row["id"] for row in spans}
    for row in spans:
        if row.get("parent_span_id"):
            assert row["parent_span_id"] in ids


def test_every_span_names_a_real_audit_event(store, make_gateway, make_tavily,
                                             claim_payload):
    """The trace is a projection of the rail. A span with no rail row is a
    second account of the run, which is how the two would diverge."""
    run, _ = _run(store, make_gateway, make_tavily, claim_payload=claim_payload)
    _, spans = _spans(store, run.run_id)
    known = {str(event["id"]) for event in store.list_events(run.run_id)}

    linked = [row for row in spans if row.get("event_id")]
    assert len(linked) >= len(spans) - 1, (
        "every span but a cache miss should name its event")
    for row in linked:
        assert str(row["event_id"]) in known, (
            f"{row['stage']}/{row['operation']} points at event "
            f"{row['event_id']}, which the rail does not hold")


def test_the_tree_groups_model_cost_under_the_stage_that_spent_it(
        store, make_gateway, make_tavily, claim_payload):
    run, _ = _run(store, make_gateway, make_tavily, claim_payload=claim_payload)
    _, spans = _spans(store, run.run_id)
    summary = summarise(spans)

    stages = {bucket["stage"]: bucket for bucket in summary["stages"]}
    assert "extract" in stages and "research" in stages
    assert stages["extract"]["spans"] == 1, "extraction is one call, one span"
    assert stages["extract"]["input_tokens"] == 10
    assert summary["totals"]["spans"] == len(spans)

    # The fake gateway prices every call at zero, and says so: a zero-cost span
    # whose source is `none` is unpriced, not free.
    assert summary["totals"]["gateway_usd"] == 0.0
    assert summary["totals"]["estimated_usd"] == 0.0


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------

def test_a_replay_from_the_same_state_reproduces_the_same_tree(
        tmp_path, make_gateway, make_tavily, claim_payload):
    """Same spans, same parents, same order.

    "Same state" is a fresh store with the same scripted answers, which is the
    only way to compare two executions of the same run. Repeating a run against
    a *warm* store is a different experiment - it reuses evidence, and the tree
    is supposed to say so, which `test_a_warm_store_shows_the_work_it_reused`
    pins separately.
    """
    prints = []
    for index in range(2):
        store = LocalStore(str(tmp_path / f"replay-{index}"))
        run, _ = _run(store, make_gateway, make_tavily,
                      claim_payload=claim_payload)
        _, spans = _spans(store, run.run_id)
        prints.append(fingerprint(spans))

    assert prints[0] == prints[1]
    assert json.loads(prints[0]), "a fingerprint of nothing proves nothing"


def test_a_warm_store_shows_the_work_it_reused(tmp_path, make_gateway,
                                               make_tavily, claim_payload):
    """Reuse changes the tree on purpose, and the change is visible.

    A second run over the same answer finds the evidence it stored before, so
    it does not search. Pretending the two trees were identical would mean
    hiding the saving; pretending the saving did not change the tree would mean
    the tree is decoration.
    """
    store = LocalStore(str(tmp_path / "warm"))
    first, _ = _run(store, make_gateway, make_tavily, claim_payload=claim_payload)
    second, _ = _run(store, make_gateway, make_tavily, claim_payload=claim_payload)

    _, first_spans = _spans(store, first.run_id)
    _, second_spans = _spans(store, second.run_id)
    first_ops = {(row["stage"], row["operation"]) for row in first_spans}
    second_ops = {(row["stage"], row["operation"]) for row in second_spans}

    assert ("research", "reuse_evidence") in second_ops
    assert ("research", "search") not in second_ops
    assert ("research", "search") in first_ops
    reused = [row for row in second_spans
              if row["operation"] == "reuse_evidence"]
    assert reused and reused[0]["cache_status"] == "cached"


def test_a_snapshot_run_says_snapshot_on_its_spans(store, make_gateway,
                                                    make_tavily, claim_payload):
    run, _ = _run(store, make_gateway, make_tavily, claim_payload=claim_payload,
                  status="snapshot")
    _, spans = _spans(store, run.run_id)
    model_spans = [row for row in spans if row["operation"] in {
        "model_call", "judge_evidence"}]
    assert model_spans
    assert all(row["cache_status"] == "snapshot" for row in model_spans), \
        "a replayed call must not be recorded as a fresh one"


# ---------------------------------------------------------------------------
# failure is a span, not a gap
# ---------------------------------------------------------------------------

def test_a_model_failure_is_a_span_with_an_error_type(
        store, make_gateway, make_tavily, claim_payload):
    run, _ = _run(store, make_gateway, make_tavily, claim_payload=claim_payload,
                  fail_stage="extract_claims")
    header, spans = _spans(store, run.run_id)

    failed = [row for row in spans if row["error_type"]]
    assert len(failed) == 1, failed
    assert failed[0]["operation"] == "model_call"
    assert failed[0]["error_type"] == "fatal"
    assert failed[0]["event_id"], "the failure span names its event"
    assert header["status"] == "failed"
    assert store.get_run(run.run_id)["status"] == "failed"


def test_a_failed_search_keeps_the_search_span(
        store, make_gateway, make_tavily, claim_payload):
    run = _start(store)
    gateway = make_gateway(claims=[claim_payload()])
    Verifier(store, run.run_id, bifrost=gateway,
             tavily=make_tavily(fail_search=True)).execute()

    _, spans = _spans(store, run.run_id)
    searches = [row for row in spans if row["operation"] == "search"]
    assert searches and searches[0]["error_type"] == "transient"


def test_a_broken_trace_does_not_break_the_verdict(store, make_gateway,
                                                   make_tavily, claim_payload,
                                                   monkeypatch):
    """Losing the trace is bad. Losing the verdict because the trace failed is
    worse, so the recorder degrades and reports."""

    def _explode(*args, **kwargs):
        raise RuntimeError("the trace table is gone")

    monkeypatch.setattr(store, "add_trace_span", _explode)
    run, _ = _run(store, make_gateway, make_tavily, claim_payload=claim_payload)

    assert store.get_run(run.run_id)["status"] == "completed"
    assert store.list_claims(run.run_id)[0]["status"] == "supported"
    events = [event["event_type"] for event in store.list_events(run.run_id)]
    assert events.count("trace.failed") == 1, (
        "a broken trace is reported once, not once per span")


def test_a_span_left_open_is_written_as_a_defect(store):
    """An operation that began and never reported is exactly the silence this
    repository exists to remove."""
    from app.verify.trace import TraceRecorder

    recorder = TraceRecorder(store, "run-x", emit=lambda *a, **k: None)
    recorder.open()
    leaked = recorder.begin("research", "search", tool_name="tavily_search")
    recorder.finish("completed")

    spans = store.list_trace_spans(recorder.trace_id)
    row = next(r for r in spans if r["id"] == leaked.id)
    assert row["error_type"] == "unclosed"
    assert row["ended_at"]


def test_a_trace_span_rejects_a_column_that_does_not_exist(store):
    """A typo in a trace field is a column that stays empty forever."""
    from app.verify.trace import TraceRecorder

    recorder = TraceRecorder(store, "run-y", emit=lambda *a, **k: None)
    recorder.open()
    with pytest.raises(TypeError):
        recorder.begin("extract", "model_call", model_used="typo")


def test_tracing_off_produces_no_partial_tree(store, make_gateway, make_tavily,
                                              claim_payload, monkeypatch):
    monkeypatch.setattr(settings, "TRACE_ENABLED", False)
    run, _ = _run(store, make_gateway, make_tavily, claim_payload=claim_payload)

    assert store.get_execution_trace(run.run_id) is None
    assert store.get_run(run.run_id)["status"] == "completed"


# ---------------------------------------------------------------------------
# the prompt version cannot drift
# ---------------------------------------------------------------------------

def test_prompt_version_changes_when_a_prompt_string_changes():
    """The digest gate. Editing a prompt without bumping its version is the
    only way a version label can lie, and this fails when it happens.

    Bumping the version and forgetting the digest fails too, in the other
    direction: the new label would vouch for text nobody computed.
    """
    from app.verify import extract, relationship

    observed = {
        prompts.EXTRACT_PROMPT_VERSION: prompts.fingerprint(
            extract._SYSTEM, extract._TEMPLATE),
        prompts.REPAIR_PROMPT_VERSION: prompts.fingerprint(
            extract._REPAIR_SYSTEM, extract._REPAIR_TEMPLATE),
        prompts.PROPOSE_PROMPT_VERSION: prompts.fingerprint(
            relationship._SYSTEM, relationship._TEMPLATE),
    }
    assert observed == prompts.REGISTERED, (
        "a prompt changed without its version, or a version was added without "
        "registering the text it names")


def test_every_prompt_version_the_orchestrator_uses_is_registered():
    import pathlib
    import re

    source = (pathlib.Path(__file__).resolve().parents[2] / "app" /
              "verify" / "orchestrator.py").read_text(encoding="utf-8")
    used = set(re.findall(r"prompt_version=([A-Z_]+)", source))
    assert used, "the orchestrator names no prompt version; the test is stale"
    names = {"EXTRACT_PROMPT_VERSION", "REPAIR_PROMPT_VERSION",
             "PROPOSE_PROMPT_VERSION"}
    assert used <= names
    assert {getattr(prompts, name) for name in used} <= prompts.PROMPT_VERSIONS


def test_a_model_span_records_the_version_it_ran_under(
        store, make_gateway, make_tavily, claim_payload):
    run, _ = _run(store, make_gateway, make_tavily, claim_payload=claim_payload)
    _, spans = _spans(store, run.run_id)

    extract_span = next(row for row in spans if row["operation"] == "model_call")
    assert extract_span["prompt_version"] == prompts.EXTRACT_PROMPT_VERSION
    assert extract_span["requested_model"] == settings.BIFROST_MODEL_EXTRACTOR

    judge = next(row for row in spans if row["operation"] == "judge_evidence")
    assert judge["prompt_version"] == prompts.PROPOSE_PROMPT_VERSION
    assert judge["requested_model"] == settings.BIFROST_MODEL_JUDGE
    assert judge["resolved_model"] == "fake/model", (
        "requested and resolved are different facts; a fallback is only "
        "visible when both are recorded")


# ---------------------------------------------------------------------------
# the endpoint
# ---------------------------------------------------------------------------

def test_the_trace_endpoint_returns_the_tree_and_the_cost_split(
        store, make_gateway, make_tavily, claim_payload, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    import app.api.verify as verify_api

    run, _ = _run(store, make_gateway, make_tavily, claim_payload=claim_payload)
    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED", True)
    monkeypatch.setattr(verify_api, "_store", lambda: store)

    response = TestClient(app).get(f"/api/verify/runs/{run.run_id}/trace")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tree"]["operation"] == "run"
    assert body["spans"]
    assert body["summary"]["totals"]["spans"] == len(body["spans"])
    assert body["trace"]["status"] == "completed"
    assert "billed and estimated" in body["note"]


def test_an_untraced_run_says_it_has_no_tree_rather_than_an_empty_one(
        store, make_gateway, make_tavily, claim_payload, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    import app.api.verify as verify_api

    monkeypatch.setattr(settings, "TRACE_ENABLED", False)
    run, _ = _run(store, make_gateway, make_tavily, claim_payload=claim_payload)
    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED", True)
    monkeypatch.setattr(verify_api, "_store", lambda: store)

    body = TestClient(app).get(f"/api/verify/runs/{run.run_id}/trace").json()
    assert body["tree"] is None
    assert body["spans"] == []
    assert "no tree" in body["note"]
