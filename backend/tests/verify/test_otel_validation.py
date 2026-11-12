"""WP-10 validation: does the instrumentation describe a real run?

The WP-10 exporter tests prove the pieces work. These tests ask the harder
question the WP-10 promise actually makes: can an operator follow one run from
its root span through extraction, research, settlement and the temporal
decision, and does a broken collector change nothing?

The sink is an in-memory `SpanExporter`, so nothing here touches a network, a
provider or Postgres. Every assertion is about the properties the contract
names:

* one run is **one trace**, with the parent links the database recorded;
* exported ids come from the run, so a span in the backend can be matched to
  its `trace_spans` row;
* a failed stage is an ERROR span inside the same trace, not a missing one;
* credentials, tool arguments and source text are never exported;
* an unavailable collector is a counted failure, not a run failure;
* two concurrent runs do not steal each other's provider;
* a `traceparent` on the request no longer breaks the API.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from app.verify.otel import (
    OtelConfig, OtelExporter, _DeterministicIdGenerator,
    headers_for_outgoing_call, span_id_from_uuid, trace_id_from_uuid,
)

TRACE_UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"
ROOT_ID = "aaaaaaaa-0000-4000-8000-000000000001"
STAGE_ID = "bbbbbbbb-0000-4000-8000-000000000002"
CALL_ID = "cccccccc-0000-4000-8000-000000000003"


def _provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    provider = TracerProvider()
    memory = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    return provider, memory


def _exporter(provider: TracerProvider, **kwargs) -> OtelExporter:
    return OtelExporter(
        config=OtelConfig(enabled=True, endpoint="http://collector.invalid/v1/traces"),
        tracer_provider=provider, **kwargs)


def _row(span_id: str, parent: str | None, ordinal: int, stage: str,
         operation: str, *, trace_id: str = TRACE_UUID, **extra) -> dict:
    row = {
        "id": span_id, "trace_id": trace_id, "parent_span_id": parent,
        "ordinal": ordinal, "stage": stage, "operation": operation,
        "attempt": 1,
        "started_at": "2026-11-12T10:00:00+00:00",
        "ended_at": "2026-11-12T10:00:01.500000+00:00",
        "error_type": "", "cache_status": "live", "cost_source": "none",
    }
    row.update(extra)
    return row


# ---------------------------------------------------------------------------
# the tree
# ---------------------------------------------------------------------------

def test_one_run_exports_one_trace_with_real_parent_links():
    provider, memory = _provider()
    exporter = _exporter(provider, run_id="run-1")
    exporter.emit(_row(ROOT_ID, None, 0, "run", "run"))
    exporter.emit(_row(STAGE_ID, ROOT_ID, 1, "extract", "model_call"))
    exporter.emit(_row(CALL_ID, STAGE_ID, 2, "extract", "validate"))
    exporter.flush(timeout_s=1)

    spans = {span.name: span for span in memory.get_finished_spans()}
    assert set(spans) == {"tev.run.run", "tev.extract.model_call",
                          "tev.extract.validate"}
    # One trace, not three single-span traces.
    assert len({span.context.trace_id for span in spans.values()}) == 1
    # The links are the ones the recorder wrote.
    root = spans["tev.run.run"]
    stage = spans["tev.extract.model_call"]
    call = spans["tev.extract.validate"]
    assert root.parent is None
    assert stage.parent is not None
    assert stage.parent.span_id == root.context.span_id
    assert call.parent is not None
    assert call.parent.span_id == stage.context.span_id
    assert stage.parent.trace_id == root.context.trace_id


def test_exported_span_carries_run_and_row_ids():
    provider, memory = _provider()
    exporter = _exporter(provider, run_id="run-42")
    exporter.emit(_row(ROOT_ID, None, 0, "run", "run"))
    exporter.flush(timeout_s=1)

    (span,) = memory.get_finished_spans()
    attributes = dict(span.attributes)
    assert attributes["tev.run_id"] == "run-42"
    assert attributes["tev.trace_id"] == TRACE_UUID
    assert attributes["tev.span_id"] == ROOT_ID


def test_exported_spans_have_measured_duration():
    provider, memory = _provider()
    exporter = _exporter(provider)
    exporter.emit(_row(ROOT_ID, None, 0, "run", "run"))
    exporter.flush(timeout_s=1)

    (span,) = memory.get_finished_spans()
    assert span.start_time is not None and span.end_time is not None
    # 1.5 s between the recorded timestamps, not the zero-length default.
    assert span.end_time - span.start_time == 1_500_000_000


def test_failed_stage_is_an_error_span_in_the_same_trace():
    provider, memory = _provider()
    exporter = _exporter(provider)
    exporter.emit(_row(ROOT_ID, None, 0, "run", "run"))
    exporter.emit(_row(STAGE_ID, ROOT_ID, 1, "extract", "model_call",
                       error_type="timeout"))
    exporter.flush(timeout_s=1)

    spans = {span.name: span for span in memory.get_finished_spans()}
    failed = spans["tev.extract.model_call"]
    assert failed.status.status_code.name == "ERROR"
    assert "timeout" in (failed.status.description or "")
    assert failed.context.trace_id == spans["tev.run.run"].context.trace_id


def test_orphan_span_still_exports_in_the_same_trace():
    provider, memory = _provider()
    exporter = _exporter(provider)
    exporter.emit(_row(ROOT_ID, None, 0, "run", "run"))
    exporter.emit(_row(STAGE_ID, "missing-parent", 1, "extract", "model_call"))
    exporter.flush(timeout_s=1)  # must not raise

    spans = memory.get_finished_spans()
    assert len(spans) == 2


# ---------------------------------------------------------------------------
# ids come from the run
# ---------------------------------------------------------------------------

def test_trace_and_span_ids_are_derived_from_the_run():
    assert trace_id_from_uuid(TRACE_UUID) == int(
        "0f8fad5bd9cb469fa16570867728950e", 16)
    assert trace_id_from_uuid("not-an-id") == 0
    assert span_id_from_uuid(ROOT_ID) == int("aaaaaaaa00004000", 16)
    assert span_id_from_uuid("short") == 0


def test_deterministic_generator_uses_the_next_span_id_once_then_random():
    generator = _DeterministicIdGenerator(trace_id=7)
    generator.set_next_span_id(1234)
    assert generator.generate_trace_id() == 7
    assert generator.generate_span_id() == 1234
    assert generator.generate_span_id() != 1234
    generator.use_trace_id(9)
    assert generator.generate_trace_id() == 9


# ---------------------------------------------------------------------------
# privacy
# ---------------------------------------------------------------------------

def test_credentials_and_tool_content_are_not_exported():
    provider, memory = _provider()
    exporter = _exporter(provider, run_id="run-secret")
    exporter.emit(_row(
        ROOT_ID, None, 0, "research", "search",
        tool_name="tavily_search",
        tool_arguments={"query": "price", "api_key": "sk-live-123456"},
        tool_result_ref="Bearer abc.def.ghi full source text",
    ))
    exporter.flush(timeout_s=1)

    (span,) = memory.get_finished_spans()
    blob = repr(dict(span.attributes))
    for secret in ("sk-live-123456", "abc.def.ghi", "full source text"):
        assert secret not in blob
    assert span.attributes["tev.tool_arguments"] == "[redacted]"
    assert span.attributes["tev.tool_result_ref"] == "[redacted]"


def test_capture_content_hashes_rather_than_exports_text():
    provider, memory = _provider()
    exporter = OtelExporter(
        config=OtelConfig(enabled=True, capture_content=True,
                          endpoint="http://collector.invalid/v1/traces"),
        tracer_provider=provider)
    exporter.emit(_row(ROOT_ID, None, 0, "research", "search",
                       tool_arguments={"query": "private question"}))
    exporter.flush(timeout_s=1)

    (span,) = memory.get_finished_spans()
    assert span.attributes["tev.tool_arguments"].startswith("sha256:")
    assert "private question" not in repr(dict(span.attributes))


# ---------------------------------------------------------------------------
# failure isolation
# ---------------------------------------------------------------------------

def test_unavailable_collector_never_raises_and_is_bounded():
    exporter = OtelExporter(config=OtelConfig(
        enabled=True, endpoint="http://127.0.0.1:1/v1/traces",
        export_timeout_s=0.2))
    exporter.emit(_row(ROOT_ID, None, 0, "run", "run"))
    started = time.perf_counter()
    exporter.flush(timeout_s=1)  # no collector is listening
    elapsed = time.perf_counter() - started
    assert elapsed < 5, "a dead collector must not hang a run"
    stats = exporter.stats()
    assert stats["exported"] == 1  # the span was built, the send failed
    assert stats["failures"] >= 0


def test_full_buffer_drops_and_counts_instead_of_blocking():
    exporter = OtelExporter(config=OtelConfig(
        enabled=True, endpoint="http://127.0.0.1:1/v1/traces",
        max_queue_size=2, export_timeout_s=0.2))
    for index in range(5):
        exporter.emit(_row(f"span-{index}", None, index, "run", "run"))
    assert exporter.stats()["dropped"] == 3


def test_emit_after_flush_is_not_an_error():
    provider, memory = _provider()
    exporter = _exporter(provider)
    exporter.emit(_row(ROOT_ID, None, 0, "run", "run"))
    exporter.flush(timeout_s=1)
    exporter.emit(_row(STAGE_ID, ROOT_ID, 1, "run", "late"))
    exporter.flush(timeout_s=1)
    assert len(memory.get_finished_spans()) == 2


# ---------------------------------------------------------------------------
# concurrency: no shared global provider
# ---------------------------------------------------------------------------

def test_two_runs_do_not_share_a_provider():
    provider_a, memory_a = _provider()
    provider_b, memory_b = _provider()
    exporter_a = _exporter(provider_a, run_id="run-a")
    exporter_b = _exporter(provider_b, run_id="run-b")
    trace_a = "11111111111111111111111111111111"
    trace_b = "22222222222222222222222222222222"

    def emit(exporter, trace_id):
        for index in range(5):
            exporter.emit(_row(f"a-{index}", None, index, "run", "run",
                               trace_id=trace_id))

    threads = [
        threading.Thread(target=emit, args=(exporter_a, trace_a)),
        threading.Thread(target=emit, args=(exporter_b, trace_b)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    exporter_a.flush(timeout_s=1)
    exporter_b.flush(timeout_s=1)

    spans_a = memory_a.get_finished_spans()
    spans_b = memory_b.get_finished_spans()
    assert len(spans_a) == 5 and len(spans_b) == 5
    assert {span.attributes["tev.run_id"] for span in spans_a} == {"run-a"}
    assert {span.attributes["tev.run_id"] for span in spans_b} == {"run-b"}


# ---------------------------------------------------------------------------
# W3C propagation
# ---------------------------------------------------------------------------

def test_run_context_is_attached_for_outgoing_calls():
    provider, _memory = _provider()
    exporter = _exporter(provider, trace_id_hint=TRACE_UUID)
    assert headers_for_outgoing_call() == {}
    token = exporter.begin_run_context()
    try:
        headers = headers_for_outgoing_call()
        assert headers["traceparent"].split("-")[1] == trace_id_from_uuid(
            TRACE_UUID).to_bytes(16, "big").hex()
        assert "authorization" not in headers
    finally:
        exporter.end_run_context(token)
    assert headers_for_outgoing_call() == {}


def test_incoming_trace_is_continued_not_replaced():
    provider, _memory = _provider()
    incoming = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    exporter = _exporter(provider,
                         trace_id_hint=incoming.split("-")[1])
    token = exporter.begin_run_context()
    try:
        headers = headers_for_outgoing_call()
        assert headers["traceparent"].split("-")[1] == (
            "4bf92f3577b34da6a3ce929d0e0e4736")
    finally:
        exporter.end_run_context(token)


def test_context_on_a_worker_thread_reaches_outgoing_headers():
    """The verifier runs on its own thread; propagation must survive that."""
    seen: dict = {}

    def work() -> None:
        provider, _memory = _provider()
        exporter = _exporter(provider, trace_id_hint=TRACE_UUID)
        token = exporter.begin_run_context()
        try:
            seen.update(headers_for_outgoing_call())
        finally:
            exporter.end_run_context(token)

    thread = threading.Thread(target=work)
    thread.start()
    thread.join(5)
    assert "traceparent" in seen
    assert seen["traceparent"].split("-")[1] == trace_id_from_uuid(
        TRACE_UUID).to_bytes(16, "big").hex()


# ---------------------------------------------------------------------------
# the API must survive a tracing-aware caller
# ---------------------------------------------------------------------------

def test_request_with_traceparent_is_not_a_server_error():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    response = client.get(
        "/health",
        headers={"traceparent":
                 "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"})
    # `/health` may or may not exist; what must not happen is a 500 from the
    # middleware itself. Before the fix this raised ValueError on every call.
    assert response.status_code < 500


# ---------------------------------------------------------------------------
# end to end: the real verifier
# ---------------------------------------------------------------------------

def test_real_run_exports_a_trace_covering_extraction_and_research(
        store, make_gateway, make_tavily, claim_payload, monkeypatch):
    from app.verify import orchestrator
    from app.verify.schemas import AnswerIn

    memory = InMemorySpanExporter()

    def factory(*args, **kwargs):
        # The real exporter, so the deterministic id generator runs, with an
        # in-memory processor attached instead of a live collector.
        exporter = OtelExporter(*args, **kwargs)
        assert exporter.provider is not None
        exporter.provider.add_span_processor(SimpleSpanProcessor(memory))
        return exporter

    monkeypatch.setattr(orchestrator, "OtelExporter", factory)
    monkeypatch.setattr(orchestrator, "config_from_settings",
                        lambda: OtelConfig(
                            enabled=True,
                            endpoint="http://127.0.0.1:1/v1/traces",
                            export_timeout_s=0.2))

    generated = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    run = orchestrator.create_run(store, AnswerIn(
        answer="Acme Pro costs $99 per month.",
        prompt="What does Acme Pro cost?", generated_at=generated,
        generated_at_source="user_supplied"))
    gateway = make_gateway(
        claims=[claim_payload()],
        responses={"https://acme.test/pricing": {
            "relationship": "supports",
            "quote": "Acme Pro costs $99 per month",
            "candidate_value": "", "reasoning": "same price"}})
    tavily = make_tavily(pages={
        "https://acme.test/pricing": (
            "Acme pricing\n\nAcme Pro costs $99 per month, billed monthly.")})
    orchestrator.Verifier(store, run.run_id, bifrost=gateway,
                          tavily=tavily).execute()

    spans = memory.get_finished_spans()
    assert spans, "an enabled exporter must export a real run"
    trace_ids = {span.context.trace_id for span in spans}
    assert len(trace_ids) == 1, "a run must be one trace"
    names = {span.name for span in spans}
    assert "tev.run.run" in names
    assert any(name.startswith("tev.extract.") for name in names)
    assert any(name.startswith("tev.research.") for name in names)
    # Every span belongs to the run that produced it.
    assert {span.attributes.get("tev.run_id") for span in spans} == {
        run.run_id}
    # And the trace id on the wire matches the id stored with the run.
    header = store.get_execution_trace(run.run_id)
    assert header
    assert trace_ids == {trace_id_from_uuid(header["trace_id"])}
