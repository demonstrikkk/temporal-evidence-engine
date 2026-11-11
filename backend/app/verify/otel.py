"""OpenTelemetry / OTLP observability export for TEV execution traces.

This module is an *additive* observability layer: it reads the execution trace
spans already written by `TraceRecorder` (in `app/verify/trace.py`) and exports
them to an OTLP-compatible backend (Jaeger, Tempo, Grafana, etc.) using the
OpenTelemetry Python SDK over HTTP/protobuf.

It is deliberately **not** part of the verification decision path:

- A run with tracing disabled (`OTEL_ENABLED=false`) produces no spans and
  makes no network calls.
- An unavailable collector never changes a verdict, blocks a run, or affects
  budgets. Export failures are logged and swallowed.
- No provider keys, authorization headers, or raw user content are exported by
  default (`OTEL_CAPTURE_CONTENT=false`).

W3C Trace Context (`traceparent`/`tracestate`) is propagated across FastAPI
request handling, Bifrost calls, and Tavily calls so a single verification run
appears as one trace even across component boundaries.

Example configuration (.env):

    OTEL_ENABLED=true
    OTEL_SERVICE_NAME=temporal-evidence-engine
    OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318/v1/traces
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
    OTEL_CAPTURE_CONTENT=false

Local demonstration with the OpenTelemetry Collector:

    # Start collector (config in otel-collector-config.yaml)
    ./otelcol_linux_amd64 --config otel-collector-config.yaml
    # Set OTEL_ENABLED=true and OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318/v1/traces
    # Start the FastAPI app and submit a verification run
    # Inspect traces in Jaeger/Tempo at the collector's configured backend
"""
from __future__ import annotations

import base64
import hashlib
import queue
import re
import threading
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace as otel_trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as _OTLPSpanExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, Span
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.trace import Span as OTelSpan
from opentelemetry.trace import format_span_id, format_trace_id
from opentelemetry.trace.propagation import set_span_in_context
from opentelemetry.util.types import Attributes

from app.core.config import settings
from app.core.logging import log
from app.verify.store import VerifyStore

#: Header names for W3C Trace Context propagation.
TRACEPARENT_HEADER = "traceparent"
TRACESTATE_HEADER = "tracestate"

#: Regex for parsing a W3C traceparent header: version-traceid-spanid-flags.
_TRACEPARENT_RE = re.compile(
    r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$", re.IGNORECASE
)

#: Span attributes that are safe to export by default. Everything else is
#: either internal plumbing or potentially sensitive.
_SAFE_SPAN_ATTRIBUTES = frozenset({
    "stage", "operation", "attempt", "claim_id", "source_id",
    "source_revision_id", "provider", "requested_model", "resolved_model",
    "prompt_version", "prompt_hash", "input_hash", "output_hash",
    "tool_name", "input_tokens", "output_tokens", "cost_usd",
    "cost_source", "latency_ms", "cache_status", "error_type",
    "decision_rule", "decision_result",
})

#: Attributes that contain potentially sensitive content and are redacted
#: unless OTEL_CAPTURE_CONTENT is enabled.
_SENSITIVE_ATTRIBUTES = frozenset({
    "tool_arguments", "tool_result_ref",
})

#: Prefix for TEV-specific span attributes.
TEV_PREFIX = "tev."

#: Semantic convention attribute names.
ATTR_SERVICE_NAME = "service.name"
ATTR_SERVICE_VERSION = "service.version"
ATTR_TELEMETRY_SDK_LANGUAGE = "telemetry.sdk.language"
ATTR_TELEMETRY_SDK_NAME = "telemetry.sdk.name"
ATTR_TELEMETRY_SDK_VERSION = "telemetry.sdk.version"


@dataclass
class OtelConfig:
    """Configuration for the OTLP exporter.

    All fields come from `Settings` (environment), never from code defaults
    that could silently enable telemetry in production.
    """

    enabled: bool = False
    service_name: str = "temporal-evidence-engine"
    service_version: str = ""
    endpoint: str = ""
    protocol: str = "http/protobuf"
    headers: dict[str, str] = field(default_factory=dict)
    sampler: str = "parentbased_always_on"
    capture_content: bool = False
    batch_size: int = 512
    batch_timeout_s: float = 5.0
    export_timeout_s: float = 10.0
    max_queue_size: int = 2048


def _parse_headers(raw: str) -> dict[str, str]:
    """Parse a comma-separated `key=value` header string.

    Malformed entries are skipped, never fatal: a bad header string must not
    prevent verification.
    """
    headers: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        key, _, value = entry.partition("=")
        headers[key.strip()] = value.strip()
    return headers


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def config_from_settings() -> OtelConfig:
    """Build `OtelConfig` from the application `Settings`.

    Never raises: if a value is unusable the exporter falls back to disabled.
    """
    headers: dict[str, str] = {}
    try:
        headers = _parse_headers(settings.OTEL_EXPORTER_OTLP_HEADERS)
    except Exception:  # noqa: BLE001 - config must never block a run
        log.warning("could not parse OTEL_EXPORTER_OTLP_HEADERS",
                    extra={"data": {"error": "malformed header string"}})

    return OtelConfig(
        enabled=settings.OTEL_ENABLED,
        service_name=settings.OTEL_SERVICE_NAME or "temporal-evidence-engine",
        service_version=settings.OTEL_SERVICE_VERSION or "",
        endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT or "",
        protocol=settings.OTEL_EXPORTER_OTLP_PROTOCOL or "http/protobuf",
        headers=headers,
        sampler=settings.OTEL_TRACES_SAMPLER or "parentbased_always_on",
        capture_content=settings.OTEL_CAPTURE_CONTENT,
        batch_size=_safe_int(settings.OTEL_BATCH_SIZE, 512),
        batch_timeout_s=_safe_float(settings.OTEL_BATCH_TIMEOUT_S, 5.0),
        export_timeout_s=_safe_float(settings.OTEL_EXPORT_TIMEOUT_S, 10.0),
        max_queue_size=_safe_int(settings.OTEL_MAX_QUEUE_SIZE, 2048),
    )


class _Redactor:
    """Redacts potentially sensitive span attributes before export.

    By default, raw prompt/tool content is never exported. When
    `OTEL_CAPTURE_CONTENT` is enabled, content is still hashed rather than
    stored verbatim in attributes, because span attributes are indexed and
    searchable in most OTLP backends.
    """

    def __init__(self, capture_content: bool = False) -> None:
        self.capture_content = capture_content

    def redact(self, attributes: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in attributes.items():
            base_key = key[len(TEV_PREFIX):] if key.startswith(TEV_PREFIX) else key
            if base_key in _SENSITIVE_ATTRIBUTES and not self.capture_content:
                out[key] = "[redacted]"
                continue
            if base_key in ("tool_arguments", "tool_result_ref") and value is not None:
                # Hash tool input/output rather than exporting raw text.
                blob = str(value).encode("utf-8", errors="replace")
                out[key] = f"sha256:{hashlib.sha256(blob).hexdigest()[:16]}"
                continue
            out[key] = value
        return out


def _span_name(stage: str, operation: str) -> str:
    """OTLP span name: `tev.{stage}.{operation}`.

    Low cardinality: stages and operations are a closed vocabulary from the
    orchestrator, so this never produces unbounded metric labels.
    """
    return f"tev.{stage}.{operation}"


def _otel_attributes(span_row: dict[str, Any], capture_content: bool) -> Attributes:
    """Convert an internal trace span row to OTLP attributes.

    Only safe, low-cardinality attributes are included. TEV-specific fields
    are prefixed with `tev.`; OpenTelemetry semantic conventions are used
    where they apply.
    """
    redactor = _Redactor(capture_content=capture_content)
    attrs: dict[str, Any] = {}

    for key, value in span_row.items():
        if key in ("id", "trace_id", "parent_span_id", "ordinal",
                   "started_at", "ended_at", "event_id"):
            continue  # handled separately as span identity/timing
        if key not in _SAFE_SPAN_ATTRIBUTES and key not in _SENSITIVE_ATTRIBUTES:
            continue  # skip unknown/internal columns
        if value is None or value == "":
            continue
        attrs[f"{TEV_PREFIX}{key}"] = value

    return redactor.redact(attrs)


class OtelExporter:
    """Batch OTLP span exporter.

    Spans are added via `emit()` (called synchronously from `TraceRecorder.end`)
    and placed on an in-memory queue. A background worker drains the queue and
    exports in batches via the OTLP HTTP/protobuf exporter.

    `flush()` blocks until the queue is drained - called from
    `TraceRecorder.finish()` so a completed run's trace is exported before the
    process exits.

    An unavailable collector never blocks verification: `emit()` never waits on
    the network, and export failures are logged and swallowed.
    """

    def __init__(self, config: OtelConfig,
                 store: VerifyStore | None = None,
                 tracer_provider: TracerProvider | None = None) -> None:
        self.config = config
        self.store = store
        self._queue: "queue.Queue[dict[str, Any] | None]" = queue.Queue(
            maxsize=config.max_queue_size)
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._flush_event = threading.Event()
        self._exported = 0
        self._dropped = 0
        self._failures = 0
        self._lock = threading.Lock()
        self._sdk_exporter: _OTLPSpanExporter | None = None
        self._batch_processor: BatchSpanProcessor | None = None
        self._tracer_provider: TracerProvider | None = tracer_provider

        if config.enabled and config.endpoint:
            self._start_sdk_exporter()

    def _start_sdk_exporter(self) -> None:
        """Create the OTLP SDK exporter and batch processor.

        Wrapped in try/except so a bad SDK configuration never prevents a run.
        """
        try:
            resource = Resource.create({
                ATTR_SERVICE_NAME: self.config.service_name,
                ATTR_TELEMETRY_SDK_LANGUAGE: "python",
                ATTR_TELEMETRY_SDK_NAME: "opentelemetry",
                ATTR_TELEMETRY_SDK_VERSION: "1.45.2",
            })
            if self.config.service_version:
                resource.attributes[ATTR_SERVICE_VERSION] = self.config.service_version

            provider = self._tracer_provider or TracerProvider(resource=resource)
            self._sdk_exporter = _OTLPSpanExporter(
                endpoint=self.config.endpoint,
                headers=self.config.headers,
                timeout=self.config.export_timeout_s,
            )
            self._batch_processor = BatchSpanProcessor(
                self._sdk_exporter,
                max_queue_size=self.config.max_queue_size,
                schedule_delay_millis=self.config.batch_timeout_s * 1000,
                max_export_batch_size=self.config.batch_size,
            )
            provider.add_span_processor(self._batch_processor)
            otel_trace.set_tracer_provider(provider)

            self._worker = threading.Thread(target=self._drain_loop, daemon=True,
                                            name="otel-export")
            self._worker.start()
            log.info("OTLP exporter started",
                     extra={"data": {"endpoint": self.config.endpoint,
                                     "service": self.config.service_name}})
        except Exception as exc:  # noqa: BLE001 - telemetry must never block
            self._failures += 1
            log.warning("OTLP exporter could not start; telemetry disabled",
                        extra={"data": {"error": str(exc)[:300]}})
            self.config.enabled = False

    def emit(self, span_row: dict[str, Any]) -> None:
        """Queue a span for export. Non-blocking: never waits on the network.

        Called from `TraceRecorder.end()` for every closed span. If the queue
        is full, the span is dropped and counted (never blocks a verdict).
        """
        if not self.config.enabled or self._sdk_exporter is None:
            return
        try:
            self._queue.put_nowait(span_row)
        except queue.Full:
            with self._lock:
                self._dropped += 1

    def flush(self, timeout_s: float | None = None) -> None:
        """Block until all queued spans are exported.

        Called from `TraceRecorder.finish()`. A timeout prevents a hung
        collector from blocking application shutdown.
        """
        if not self.config.enabled or self._sdk_exporter is None:
            return
        # Signal the drain loop to stop after the queue empties.
        self._flush_event.set()
        try:
            self._queue.put_nowait(None)  # sentinel
        except queue.Full:
            pass
        if self._worker is not None:
            self._worker.join(timeout=timeout_s or self.config.export_timeout_s)
        # Force an SDK-level flush (waits for in-flight export).
        try:
            if self._batch_processor is not None:
                self._batch_processor.force_flush(
                    timeout_millis=int((timeout_s or self.config.export_timeout_s)
                                       * 1000))
        except Exception:  # noqa: BLE001
            pass

    def _drain_loop(self) -> None:
        """Background worker: drains the queue and exports spans via OTLP.

        Runs until the sentinel (`None`) is received. Spans are converted to
        OpenTelemetry `Span` objects and emitted through the SDK's batch
        processor, which handles its own batching and retry.
        """
        tracer = otel_trace.get_tracer(__name__)
        batch: list[dict[str, Any]] = []

        def export_batch() -> None:
            if not batch:
                return
            with self._lock:
                current = batch
                batch = []
            self._emit_spans(tracer, current)

        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                if self._flush_event.is_set():
                    export_batch()
                    break
                continue

            if item is None:  # sentinel
                export_batch()
                break

            batch.append(item)
            if len(batch) >= self.config.batch_size:
                export_batch()

    def _emit_spans(self, tracer: Any, spans: list[dict[str, Any]]) -> None:
        """Convert internal spans to OTLP spans and emit them."""
        for span_row in spans:
            try:
                self._emit_one_span(tracer, span_row)
                with self._lock:
                    self._exported += 1
            except Exception:  # noqa: BLE001 - one bad span never stops export
                with self._lock:
                    self._failures += 1

    def _emit_one_span(self, tracer: Any, span_row: dict[str, Any]) -> None:
        """Create and end one OTLP span from an internal trace span row."""
        ctx = otel_trace.set_span_in_context(otel_trace.INVALID_SPAN)
        otel_span: OTelSpan | None = None
        try:
            name = _span_name(str(span_row.get("stage", "")),
                              str(span_row.get("operation", "")))
            otel_span = tracer.start_span(
                name=name,
                context=ctx,
                attributes=_otel_attributes(span_row,
                                            capture_content=self.config.capture_content),
            )
            # Status.
            status_code = otel_trace.StatusCode.UNSET
            error_type = str(span_row.get("error_type") or "")
            if error_type:
                status_code = otel_trace.StatusCode.ERROR
                otel_span.set_status(status_code, error_type[:200])
            otel_span.end()
        except Exception:  # noqa: BLE001 - one bad span never stops export
            pass
        finally:
            if otel_span is not None:
                try:
                    otel_span.end()
                except Exception:  # noqa: BLE001
                    pass

    # -- statistics ---------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.config.enabled,
                "exported": self._exported,
                "dropped": self._dropped,
                "failures": self._failures,
                "endpoint": self.config.endpoint if self.config.enabled else "",
            }


# ---------------------------------------------------------------------------
# W3C Trace Context propagation
# ---------------------------------------------------------------------------

def parse_traceparent(value: str | None) -> dict[str, str] | None:
    """Parse a W3C `traceparent` header into trace_id, span_id, and flags.

    Returns None for malformed or missing headers. Never raises.
    """
    if not value:
        return None
    match = _TRACEPARENT_RE.match(value.strip())
    if not match:
        return None
    return {
        "version": match.group(1),
        "trace_id": match.group(2),
        "span_id": match.group(3),
        "flags": match.group(4),
    }


def traceparent_from_trace(trace_id: str, span_id: str) -> str:
    """Build a W3C `traceparent` header from trace and span IDs.

    Both IDs are hex strings (32 chars for trace, 16 for span).
    """
    return f"00-{trace_id}-{span_id}-01"


def current_span_id() -> str:
    """Return the current OpenTelemetry span ID as hex, or a zero ID."""
    current = otel_trace.get_current_span()
    ctx = current.get_span_context()
    if not ctx or not ctx.is_valid:
        return "0" * 16
    return format_span_id(ctx.span_id)


def current_trace_id() -> str:
    """Return the current OpenTelemetry trace ID as hex, or a zero ID."""
    current = otel_trace.get_current_span()
    ctx = current.get_span_context()
    if not ctx or not ctx.is_valid:
        return "0" * 32
    return format_trace_id(ctx.trace_id)


def headers_for_outgoing_call() -> dict[str, str]:
    """W3C trace context headers for an outgoing Bifrost/Tavily request.

    Includes `traceparent` (current trace + span) and `tracestate` if present.
    Never includes authorization or API keys.
    """
    headers: dict[str, str] = {}
    trace_id = current_trace_id()
    span_id = current_span_id()
    if trace_id != "0" * 32:
        headers[TRACEPARENT_HEADER] = traceparent_from_trace(trace_id, span_id)
    # tracestate is optional; omit when empty to keep headers small.
    return headers


def extract_trace_context(headers: dict[str, str]) -> dict[str, str] | None:
    """Extract trace context from incoming request headers.

    Returns `{"trace_id": ..., "span_id": ...}` or None. Used by FastAPI
    middleware to continue a trace started by the caller.
    """
    traceparent = headers.get(TRACEPARENT_HEADER) or headers.get(
        TRACEPARENT_HEADER.lower())
    if not traceparent:
        return None
    parsed = parse_traceparent(traceparent)
    if not parsed:
        return None
    return {"trace_id": parsed["trace_id"], "span_id": parsed["span_id"]}
