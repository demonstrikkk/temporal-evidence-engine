"""What WP-10 costs per call, measured rather than assumed.

The claims that need numbers:

* `emit()` is on the hot path (`TraceRecorder.end` calls it for every span), so
  it must be cheap even when telemetry is on;
* `headers_for_outgoing_call()` runs on every Bifrost and Tavily request;
* `flush()` happens once per run, so its cost is a run-finalisation cost, not a
  per-claim one.

The measurement uses an in-memory span sink, so the numbers are the
instrumentation's cost, not a collector's. The point is the ratio between
"telemetry off" and "telemetry on", not an absolute wall-clock budget.

Usage (from the repository root):

    backend\\.venv\\Scripts\\python.exe scripts\\otel_overhead.py
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

from app.verify.otel import (  # noqa: E402
    OtelConfig, OtelExporter, headers_for_outgoing_call,
)

SPANS = 20_000
RUNS = 5


def _row(index: int) -> dict:
    return {
        "id": f"span-{index}", "trace_id": "0f8fad5b-d9cb-469f-a165-70867728950e",
        "parent_span_id": None, "ordinal": index, "stage": "extract",
        "operation": "model_call", "attempt": 1,
        "started_at": "2026-11-12T10:00:00+00:00",
        "ended_at": "2026-11-12T10:00:00.010000+00:00",
        "input_tokens": 120, "output_tokens": 40, "cost_usd": 0.0004,
        "cost_source": "gateway", "latency_ms": 10, "cache_status": "live",
        "error_type": "", "tool_arguments": {"query": "x" * 120},
    }


def _emit_cost(config: OtelConfig, provider: TracerProvider | None) -> float:
    exporter = OtelExporter(config=config, tracer_provider=provider)
    rows = [_row(index) for index in range(SPANS)]
    started = time.perf_counter_ns()
    for row in rows:
        exporter.emit(row)
    return (time.perf_counter_ns() - started) / SPANS


def main() -> int:
    off = []
    on = []
    for _ in range(RUNS):
        off.append(_emit_cost(OtelConfig(enabled=False), None))
        provider = TracerProvider()
        memory = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(memory))
        on.append(_emit_cost(
            OtelConfig(enabled=True, endpoint="http://collector.invalid/v1/traces"),
            provider))
    off_med = statistics.median(off)
    on_med = statistics.median(on)

    provider = TracerProvider()
    memory = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    exporter = OtelExporter(
        config=OtelConfig(enabled=True, endpoint="http://collector.invalid/v1/traces"),
        tracer_provider=provider, run_id="run-overhead")
    rows = [_row(index) for index in range(100)]
    for row in rows:
        exporter.emit(row)
    started = time.perf_counter_ns()
    exporter.flush(timeout_s=1)
    flush_ms = (time.perf_counter_ns() - started) / 1_000_000
    exported = len(memory.get_finished_spans())

    header_runs = []
    for _ in range(RUNS):
        started = time.perf_counter_ns()
        for _index in range(SPANS):
            headers_for_outgoing_call()
        header_runs.append((time.perf_counter_ns() - started) / SPANS)
    header_med = statistics.median(header_runs)

    print(f"emit, telemetry off        {off_med:8.0f} ns/span")
    print(f"emit, telemetry on         {on_med:8.0f} ns/span")
    print(f"emit overhead              {on_med - off_med:8.0f} ns/span "
          f"({(on_med / off_med) if off_med else 0:.1f}x)")
    print(f"{SPANS} spans, telemetry on    "
          f"{(on_med * SPANS) / 1_000_000:8.1f} ms total")
    print(f"flush, 100-span tree       {flush_ms:8.2f} ms "
          f"({exported} spans exported)")
    print(f"outgoing header build      {header_med:8.0f} ns/call")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
