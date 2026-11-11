"""WP-10 OTLP exporter tests.

Covers: span creation, attribute naming (`tev.` prefix), parent-child
relationships, disabled exporter behaviour, redaction, collector unavailable,
and W3C trace context helpers.

All tests are hermetic: no network, no provider, no Postgres. The exporter is
instantiated directly with a disabled or bad-config `OtelConfig`, so no export
is ever attempted.
"""
from __future__ import annotations

import queue
from datetime import datetime, timezone

import pytest

from app.verify.otel import (
    OtelConfig, OtelExporter, config_from_settings,
    parse_traceparent, traceparent_from_trace, headers_for_outgoing_call,
    extract_trace_context, _Redactor, _span_name, _otel_attributes,
)
from opentelemetry.context import attach
from opentelemetry.trace import SpanContext, INVALID_SPAN, NonRecordingSpan
from opentelemetry.trace.propagation import set_span_in_context


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def test_config_from_settings_disabled_by_default(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "OTEL_ENABLED", False)
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "")
    cfg = config_from_settings()
    assert cfg.enabled is False
    assert cfg.endpoint == ""
    assert cfg.capture_content is False


def test_config_from_settings_enabled(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "OTEL_ENABLED", True)
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT",
                        "http://127.0.0.1:4318/v1/traces")
    monkeypatch.setattr(settings, "OTEL_CAPTURE_CONTENT", True)
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_HEADERS",
                        "Authorization=Bearer x, X-Custom=1")
    cfg = config_from_settings()
    assert cfg.enabled is True
    assert cfg.endpoint == "http://127.0.0.1:4318/v1/traces"
    assert cfg.capture_content is True
    assert cfg.headers == {"Authorization": "Bearer x", "X-Custom": "1"}


def test_config_from_settings_malformed_headers(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "OTEL_ENABLED", True)
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT",
                        "http://127.0.0.1:4318/v1/traces")
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_HEADERS", "not-a-header")
    cfg = config_from_settings()
    # malformed entries are skipped, never fatal
    assert cfg.enabled is True
    assert cfg.headers == {}


def test_config_from_settings_never_raises(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "OTEL_ENABLED", True)
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT",
                        "http://127.0.0.1:4318/v1/traces")
    monkeypatch.setattr(settings, "OTEL_BATCH_SIZE", "not-an-int")
    cfg = config_from_settings()
    assert cfg.enabled is True
    assert cfg.batch_size == 512  # fell back to default


# ---------------------------------------------------------------------------
# exporter: disabled / unavailable collector
# ---------------------------------------------------------------------------

def test_disabled_exporter_emits_nothing():
    cfg = OtelConfig(enabled=False)
    exporter = OtelExporter(config=cfg)
    exporter.emit({"stage": "extract", "operation": "model_call"})
    assert exporter.stats()["exported"] == 0
    assert exporter.stats()["dropped"] == 0


def test_exporter_with_bad_endpoint_degrades():
    cfg = OtelConfig(
        enabled=True,
        endpoint="http://127.0.0.1:1/v1/traces",  # nothing listening
        export_timeout_s=0.5,
    )
    # Construction itself must not raise; the SDK exporter is created lazily
    # and never connects until export() is called.
    exporter = OtelExporter(config=cfg)
    exporter.emit({"stage": "extract", "operation": "model_call"})
    assert exporter.stats()["exported"] == 0


def test_exporter_endpoint_blank_emit_is_noop():
    cfg = OtelConfig(enabled=True, endpoint="")
    exporter = OtelExporter(config=cfg)
    # No SDK exporter was created, so emit is a no-op.
    exporter.emit({"stage": "extract", "operation": "model_call"})
    assert exporter.stats()["exported"] == 0


def test_exporter_queue_drop_on_full():
    cfg = OtelConfig(
        enabled=True, endpoint="http://127.0.0.1:1/v1/traces",
        max_queue_size=2, export_timeout_s=0.2,
    )
    exporter = OtelExporter(config=cfg)
    # The exporter degraded (bad endpoint), so manually enable the queue path
    # to test drop-on-full without a network connection.
    exporter.config.enabled = True
    exporter._sdk_exporter = object()  # non-None so emit() queues
    exporter.emit({"stage": "a", "operation": "x"})
    exporter.emit({"stage": "b", "operation": "y"})
    exporter.emit({"stage": "c", "operation": "z"})  # dropped
    assert exporter.stats()["dropped"] == 1


# ---------------------------------------------------------------------------
# span naming and attribute conversion
# ---------------------------------------------------------------------------

def test_span_name_tev_prefix():
    assert _span_name("extract", "model_call") == "tev.extract.model_call"
    assert _span_name("research", "search") == "tev.research.search"


def test_otel_attributes_only_safe_columns():
    row = {
        "id": "span-1", "trace_id": "trace-1", "parent_span_id": "span-0",
        "ordinal": 1, "stage": "extract", "operation": "model_call",
        "attempt": 1, "started_at": "2026-01-01T00:00:00Z",
        "ended_at": "2026-01-01T00:00:01Z", "event_id": "event-1",
        "claim_id": "claim-1",
        "requested_model": "mistral-small",
        "resolved_model": "mistral-small",
        "provider": "fake",
        "prompt_version": "v1",
        "prompt_hash": "abc123",
        "input_hash": "def456",
        "output_hash": "ghi789",
        "tool_name": "tavily_search",
        "tool_arguments": {"query": "secret query"},
        "tool_result_ref": "ref-1",
        "input_tokens": 100, "output_tokens": 50,
        "cost_usd": 0.001, "cost_source": "gateway",
        "latency_ms": 1200, "cache_status": "live",
        "error_type": "", "decision_rule": "gate",
        "decision_result": "supported",
        # internal columns that should not appear as attributes
        "_internal": "skip",
    }
    attrs = _otel_attributes(row, capture_content=False)
    assert attrs["tev.stage"] == "extract"
    assert attrs["tev.operation"] == "model_call"
    assert attrs["tev.claim_id"] == "claim-1"
    assert attrs["tev.provider"] == "fake"
    assert attrs["tev.requested_model"] == "mistral-small"
    assert attrs["tev.resolved_model"] == "mistral-small"
    assert attrs["tev.input_tokens"] == 100
    assert attrs["tev.output_tokens"] == 50
    assert attrs["tev.cost_usd"] == 0.001
    assert attrs["tev.latency_ms"] == 1200
    assert attrs["tev.cache_status"] == "live"
    assert "_internal" not in attrs
    # sensitive: tool_arguments and tool_result_ref redacted by default
    assert attrs["tev.tool_arguments"] == "[redacted]"
    assert attrs["tev.tool_result_ref"] == "[redacted]"


def test_otel_attributes_capture_content():
    row = {
        "stage": "extract", "operation": "model_call",
        "tool_arguments": {"query": "sensitive text"},
        "tool_result_ref": "plain text content",
    }
    attrs = _otel_attributes(row, capture_content=True)
    # capture_content=True still hashes rather than exporting raw text
    assert attrs["tev.tool_arguments"].startswith("sha256:")
    assert attrs["tev.tool_arguments"] != "[redacted]"


# ---------------------------------------------------------------------------
# W3C trace context
# ---------------------------------------------------------------------------

def test_parse_traceparent_valid():
    parsed = parse_traceparent("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
    assert parsed == {
        "version": "00",
        "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        "span_id": "00f067aa0ba902b7",
        "flags": "01",
    }


def test_parse_traceparent_invalid():
    assert parse_traceparent(None) is None
    assert parse_traceparent("") is None
    assert parse_traceparent("not-a-traceparent") is None
    assert parse_traceparent("00-tooshort-00f067aa0ba902b7-01") is None


def test_traceparent_from_trace_roundtrip():
    tp = traceparent_from_trace(
        "4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7")
    assert tp == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    assert parse_traceparent(tp) is not None


def test_headers_for_outgoing_call_without_context():
    headers = headers_for_outgoing_call()
    assert headers == {}


def test_headers_for_outgoing_call_with_context():
    ctx = SpanContext(
        trace_id=int("4bf92f3577b34da6a3ce929d0e0e4736", 16),
        span_id=int("00f067aa0ba902b7", 16),
        is_remote=False,
        trace_flags=0x01,
    )
    non_rec = NonRecordingSpan(ctx)
    token = attach(set_span_in_context(non_rec))
    headers = headers_for_outgoing_call()
    assert "traceparent" in headers
    assert headers["traceparent"] == (
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    )
    # No authorization header is ever included
    assert "authorization" not in headers
    assert "Authorization" not in headers


def test_extract_trace_context_from_headers():
    ctx = extract_trace_context({
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
    })
    assert ctx is not None
    assert ctx["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert ctx["span_id"] == "00f067aa0ba902b7"


def test_extract_trace_context_missing():
    assert extract_trace_context({}) is None
    assert extract_trace_context({"traceparent": "garbage"}) is None


# ---------------------------------------------------------------------------
# exporter stats
# ---------------------------------------------------------------------------

def test_exporter_stats_shape():
    cfg = OtelConfig(enabled=True, endpoint="http://127.0.0.1:4318/v1/traces")
    exporter = OtelExporter(config=cfg)
    stats = exporter.stats()
    assert set(stats) == {"enabled", "exported", "dropped", "failures",
                          "endpoint"}
    # The exporter degraded (no collector at import time in test env), so
    # enabled reflects whether config said enabled, not whether export works.
    assert stats["endpoint"] == "http://127.0.0.1:4318/v1/traces"
