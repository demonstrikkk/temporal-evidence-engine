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

import hashlib
import random
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from opentelemetry import trace as otel_trace
from opentelemetry.context import Context, attach, detach
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as _OTLPSpanExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.id_generator import IdGenerator
from opentelemetry.trace import NonRecordingSpan, SpanContext, StatusCode
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

#: Sentinel marking an exporter that hands spans to an injected provider
#: instead of creating its own OTLP network exporter.
_INJECTED_EXPORTER = object()

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


# ---------------------------------------------------------------------------
# deterministic ids: the exported trace is the internal trace
# ---------------------------------------------------------------------------

def _hex_only(value: Any) -> str:
    """Keep only hexadecimal characters (a UUID becomes its 32-char form)."""
    return "".join(ch for ch in str(value or "") if ch in "0123456789abcdefABCDEF")


def trace_id_from_uuid(value: Any) -> int:
    """The 128-bit OpenTelemetry trace id carried by a run's `trace_id`.

    A run's internal `trace_id` is a UUID (128 bits). Stripping the dashes
    yields exactly the 32 hex characters a W3C trace id is made of, so the
    exported trace can be found from the database row and vice versa. Returns
    0 when the value cannot be read as an id (the exporter then generates one).
    """
    hexed = _hex_only(value)
    if len(hexed) < 32:
        return 0
    try:
        return int(hexed[:32], 16)
    except ValueError:  # pragma: no cover - guarded by the character filter
        return 0


def span_id_from_uuid(value: Any) -> int:
    """The 64-bit OpenTelemetry span id carried by an internal span id."""
    hexed = _hex_only(value)
    if len(hexed) < 16:
        return 0
    try:
        return int(hexed[:16], 16) or 1
    except ValueError:  # pragma: no cover
        return 0


def _timestamp_ns(value: Any) -> int | None:
    """Parse a stored ISO timestamp into OTel's nanosecond epoch form."""
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            stamp = value
        else:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return int(stamp.timestamp() * 1_000_000_000)
    except (TypeError, ValueError):
        return None


class _DeterministicIdGenerator(IdGenerator):
    """Ids that come from the run, not from a random number generator.

    The trace id is fixed for the run, so every span lands in one trace and
    that trace is the same one the caller started (when they sent a
    `traceparent`). Span ids are taken from the internal span ids, so a span in
    the tracing backend can be matched to the row in `trace_spans` that produced
    it. Random ids are still used whenever an id cannot be derived - an
    unusable identifier must not lose the span.
    """

    def __init__(self, trace_id: int = 0) -> None:
        self._trace_id = trace_id or 0
        self._next_span_id = 0
        self._lock = threading.Lock()

    def use_trace_id(self, trace_id: int) -> None:
        if not trace_id:
            return
        with self._lock:
            self._trace_id = trace_id

    def set_next_span_id(self, span_id: int) -> None:
        with self._lock:
            self._next_span_id = span_id or 0

    def generate_trace_id(self) -> int:
        with self._lock:
            if not self._trace_id:
                self._trace_id = random.getrandbits(128) or 1
            return self._trace_id

    def generate_span_id(self) -> int:
        with self._lock:
            span_id = self._next_span_id
            self._next_span_id = 0
            return span_id or random.getrandbits(64) or 1


class OtelExporter:
    """Run-scoped OTLP exporter: one run, one trace tree.

    Spans are collected by `emit()` (called from `TraceRecorder.end()` as each
    span closes) and the whole tree is exported by `flush()` (called from
    `TraceRecorder.finish()`). Collecting first is what makes the tree real: a
    child is always created with its parent's context, so the trace shows the
    run, its stages and their children nested the way the database recorded
    them instead of arriving as unrelated single-span traces.

    Nothing here is on the verdict path. `emit()` never waits on the network; a
    full buffer drops spans and counts them rather than blocking a run; every
    export failure is counted and swallowed. The SDK `BatchSpanProcessor` does
    the network batching, so a slow collector costs latency at flush, never a
    verdict.
    """

    def __init__(self, config: OtelConfig,
                 store: VerifyStore | None = None,
                 tracer_provider: TracerProvider | None = None,
                 run_id: str = "",
                 trace_id_hint: str = "") -> None:
        self.config = config
        self.store = store
        self.run_id = run_id
        #: W3C trace id of the caller (hex), from the run's `traceparent`.
        #: When present, the exported trace continues the caller's trace.
        self.trace_id_hint = trace_id_hint
        self._pending: list[dict[str, Any]] = []
        self._exported = 0
        self._dropped = 0
        self._failures = 0
        self._lock = threading.Lock()
        self._sdk_exporter: Any = None
        self._batch_processor: BatchSpanProcessor | None = None
        self._tracer_provider: TracerProvider | None = tracer_provider
        self._tracer: Any = None
        self.provider: TracerProvider | None = None
        self._ids: _DeterministicIdGenerator | None = None

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

            if self._tracer_provider is None:
                self._ids = _DeterministicIdGenerator(
                    trace_id_from_uuid(self.trace_id_hint))
                provider = TracerProvider(resource=resource,
                                          id_generator=self._ids)
                self._sdk_exporter = _OTLPSpanExporter(
                    endpoint=self.config.endpoint,
                    headers=self.config.headers,
                    timeout=self.config.export_timeout_s,
                )
                self._batch_processor = BatchSpanProcessor(
                    self._sdk_exporter,
                    max_queue_size=self.config.max_queue_size,
                    schedule_delay_millis=self.config.batch_timeout_s * 1000,
                    # The SDK rejects a batch larger than its queue, which used
                    # to disable telemetry outright for a small queue. Clamp
                    # instead: a config mismatch must not silence the exporter.
                    max_export_batch_size=max(1, min(self.config.batch_size,
                                                     self.config.max_queue_size)),
                )
                provider.add_span_processor(self._batch_processor)
            else:
                # An injected provider owns its ids and its exporters (tests do
                # this to capture spans). The tree is built the same way.
                provider = self._tracer_provider
                self._sdk_exporter = _INJECTED_EXPORTER
            # Deliberately not `otel_trace.set_tracer_provider`: that is a
            # process-wide singleton, so a second concurrent run would replace
            # the first run's provider and export through the wrong exporter.
            # Each run keeps its own tracer instead.
            self.provider = provider
            self._tracer = provider.get_tracer("tev.otel")
            log.info("OTLP exporter started",
                     extra={"data": {"endpoint": self.config.endpoint,
                                     "service": self.config.service_name}})
        except Exception as exc:  # noqa: BLE001 - telemetry must never block
            self._failures += 1
            log.warning("OTLP exporter could not start; telemetry disabled",
                        extra={"data": {"error": str(exc)[:300]}})
            self.config.enabled = False

    def emit(self, span_row: dict[str, Any]) -> None:
        """Collect a closed span. Non-blocking: never waits on the network.

        Called from `TraceRecorder.end()` for every closed span. If the buffer
        is full, the span is dropped and counted (never blocks a verdict).
        """
        if not self.config.enabled or self._sdk_exporter is None:
            return
        with self._lock:
            if len(self._pending) >= self.config.max_queue_size:
                self._dropped += 1
                return
            self._pending.append(span_row)

    def flush(self, timeout_s: float | None = None) -> None:
        """Export the run's collected spans as one tree, then wait for the SDK.

        Called from `TraceRecorder.finish()`. The timeout bounds how long a
        hung collector can delay run finalisation; it never turns into a run
        failure.
        """
        if not self.config.enabled or self._sdk_exporter is None:
            return
        with self._lock:
            rows, self._pending = self._pending, []
        if rows:
            self._export_rows(rows)
        try:
            if self._batch_processor is not None:
                self._batch_processor.force_flush(
                    timeout_millis=int((timeout_s or self.config.export_timeout_s)
                                       * 1000))
        except Exception:  # noqa: BLE001
            pass

    def _export_rows(self, rows: list[dict[str, Any]]) -> None:
        """Export one run's spans as a tree, parents before children."""
        ordered = sorted(rows, key=lambda row: int(row.get("ordinal") or 0))
        if self._ids is not None:
            root = next((r for r in ordered if not r.get("parent_span_id")), None)
            hinted = trace_id_from_uuid(self.trace_id_hint)
            self._ids.use_trace_id(hinted or trace_id_from_uuid(
                (root or {}).get("trace_id")))
        contexts: dict[str, Any] = {}
        for row in ordered:
            try:
                self._export_one(row, contexts)
                with self._lock:
                    self._exported += 1
            except Exception:  # noqa: BLE001 - one bad span never stops export
                with self._lock:
                    self._failures += 1

    def _export_one(self, row: dict[str, Any],
                    contexts: dict[str, Any]) -> None:
        """Create and end one OTLP span under its recorded parent."""
        if self._tracer is None:
            return
        attributes = _otel_attributes(
            row, capture_content=self.config.capture_content)
        if self.run_id:
            attributes["tev.run_id"] = self.run_id
        for column, attribute in (("trace_id", "trace_id"), ("id", "span_id"),
                                  ("parent_span_id", "parent_span_id")):
            value = row.get(column)
            if value:
                attributes[f"{TEV_PREFIX}{attribute}"] = str(value)
        if self._ids is not None:
            self._ids.set_next_span_id(span_id_from_uuid(row.get("id")))
        parent_id = str(row.get("parent_span_id") or "")
        parent = contexts.get(parent_id) if parent_id else None
        name = _span_name(str(row.get("stage") or ""),
                          str(row.get("operation") or ""))
        # An empty context (not None) keeps a root span a root: `None` would
        # adopt whatever span happens to be current on the exporting thread.
        span = self._tracer.start_span(
            name, context=parent or Context(), attributes=attributes,
            start_time=_timestamp_ns(row.get("started_at")))
        try:
            error_type = str(row.get("error_type") or "")
            if error_type:
                span.set_status(StatusCode.ERROR, error_type[:200])
            contexts[str(row.get("id") or "")] = set_span_in_context(span)
        finally:
            span.end(end_time=_timestamp_ns(row.get("ended_at")))

    # -- W3C context for outgoing calls ------------------------------------
    def begin_run_context(self, fallback_trace_id: str = "") -> Any:
        """Make the run's trace current on this thread.

        `headers_for_outgoing_call()` reads the current span, so attaching a
        non-recording span here is what puts a `traceparent` on every Bifrost
        and Tavily request - including a run started without one. Returns the
        context token, or None when no valid trace id exists.
        """
        trace_id = (trace_id_from_uuid(self.trace_id_hint)
                    or trace_id_from_uuid(fallback_trace_id))
        if not trace_id:
            return None
        context = SpanContext(
            trace_id=trace_id, span_id=random.getrandbits(64) or 1,
            is_remote=False, trace_flags=0x01)
        return attach(set_span_in_context(NonRecordingSpan(context)))

    @staticmethod
    def end_run_context(token: Any) -> None:
        """Detach a context attached by `begin_run_context`. Never raises."""
        if token is None:
            return
        try:
            detach(token)
        except Exception:  # noqa: BLE001 - a stale token must not fail a run
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
