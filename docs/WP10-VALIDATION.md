# WP-10 validation: does the trace describe a real run?

**Baseline:** `60843c1` — WP-10: Complete OTLP observability integration.
**Scope:** validation and narrowly scoped instrumentation fixes. No verdict rule,
no budget, no research path and no model call was changed.
**Reproduce:**

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\verify\test_otel_exporter.py `
  backend\tests\verify\test_otel_validation.py -q
backend\.venv\Scripts\python.exe scripts\otel_overhead.py
```

The new tests use an in-memory `SpanExporter`, so they prove the tree the
exporter builds without requiring a collector. One end-to-end test runs the real
`Verifier` through a fake gateway and Tavily, so the spans come from the actual
pipeline rather than from hand-built rows.

## Verdict

The instrumentation exported spans. It did **not** export a trace.

Every span was created with `INVALID_SPAN` as its parent
(`otel.py`, old `_emit_one_span`), so a run with 40 spans produced 40 separate
single-span traces. On top of that, `set_tracer_provider()` was called once per
run, which is a process-wide singleton: two concurrent runs replaced each
other's provider. And the incoming W3C context was built and discarded, so
neither Bifrost nor Tavily ever received a `traceparent`.

Those are fixed. The exporter now collects a run's spans and exports them as one
tree at flush, with the parent links the database recorded, deterministic ids
that match the stored rows, and real start/end timestamps. Nine defects are
recorded below with the test that pins each one.

## Defects found and fixed

| # | Defect | Evidence | Fix |
|---|--------|----------|-----|
| 1 | **Any request carrying `traceparent` returned HTTP 500.** `dict(request.headers.getlist("traceparent"))` raises `ValueError: dictionary update sequence element #0 has length 55`. | Reproduced with `TestClient`: `GET /health` + `traceparent` raised before the fix. | Read the header directly; regression test `test_request_with_traceparent_is_not_a_server_error`. |
| 2 | **Incoming trace context was never attached.** `set_span_in_context(non_rec)` was called and its result discarded, so `headers_for_outgoing_call()` always saw no current span. | `orchestrator.py` (old lines 282-292). | `begin_run_context()` attaches a non-recording span; `execute()` detaches it in `finally`. Tests `test_run_context_is_attached_for_outgoing_calls`, `test_context_on_a_worker_thread_reaches_outgoing_headers`. |
| 3 | **Exported spans were flat.** Each span was started with `INVALID_SPAN`, so every span became its own trace. | `otel.py` (old `_emit_one_span`). | Buffer rows per run; export parents before children with the recorded parent context. `test_one_run_exports_one_trace_with_real_parent_links`. |
| 4 | **Concurrent runs stole each other's provider.** `otel_trace.set_tracer_provider(provider)` is process-wide and was called per run. | `otel.py` (old `_start_sdk_exporter`). | Each exporter owns its provider and tracer; the global is never touched. `test_two_runs_do_not_share_a_provider`. |
| 5 | **`span.end()` was called twice.** Once in the `try`, once in `finally`. | `otel.py` (old `_emit_one_span`). | One `end()` in `finally`. |
| 6 | **Spans had zero duration.** `started_at`/`ended_at` were stored but never sent to OTel. | `otel.py` (old `_emit_one_span`). | Parse both timestamps into OTel nanoseconds. `test_exported_spans_have_measured_duration` asserts the 1.5 s gap. |
| 7 | **A small `OTEL_MAX_QUEUE_SIZE` silently disabled telemetry.** `BatchSpanProcessor` raises when `max_export_batch_size > max_queue_size`, the exporter caught it and set `enabled=False`. | `test_full_buffer_drops_and_counts_instead_of_blocking` was dropping 0 spans. | Clamp the batch size to the queue size. |
| 8 | **A test leaked an attached OTel context** into every later test on the thread. | `test_headers_for_outgoing_call_with_context` failed only when run after another test. | Detach in `finally` (the test itself was the defect). |
| 9 | **No way to match a span to the row that produced it.** | — | Deterministic ids (`trace_id` from the run UUID, span ids from span UUIDs) plus `tev.run_id`, `tev.trace_id`, `tev.span_id`, `tev.parent_span_id` attributes. `test_trace_and_span_ids_are_derived_from_the_run`, `test_exported_span_carries_run_and_row_ids`. |

## What the validation now proves

| Contract | Test | Result |
|----------|------|--------|
| One run is one trace | `test_one_run_exports_one_trace_with_real_parent_links` | One trace id across root, stage and child spans; parent links match the rows. |
| The root is a root | same test | `root.parent is None`; a leaf's parent is its stage, and the stage's parent is the root. |
| Real durations | `test_exported_spans_have_measured_duration` | 1.5 s between the recorded timestamps, not zero. |
| Failure is visible, not missing | `test_failed_stage_is_an_error_span_in_the_same_trace` | ERROR status with the error type, same trace as the run. |
| An orphan does not crash export | `test_orphan_span_still_exports_in_the_same_trace` | Both spans exported. |
| No credentials, no content | `test_credentials_and_tool_content_are_not_exported` | `sk-live-…`, a bearer token and source text are absent from every attribute; `tool_arguments` is `[redacted]`. |
| `OTEL_CAPTURE_CONTENT` hashes, never stores | `test_capture_content_hashes_rather_than_exports_text` | `sha256:…`; the query text is not present. |
| A dead collector changes nothing | `test_unavailable_collector_never_raises_and_is_bounded` | `flush()` returns in well under 5 s, no exception; the span was still built. |
| A full buffer drops, never blocks | `test_full_buffer_drops_and_counts_instead_of_blocking` | 3 of 5 dropped and counted, no blocking. |
| Two runs stay separate | `test_two_runs_do_not_share_a_provider` | Five spans each, each sink sees only its own `tev.run_id`. |
| Propagation reaches outgoing calls | `test_run_context_is_attached_for_outgoing_calls`, `test_context_on_a_worker_thread_reaches_outgoing_headers` | A valid `traceparent` with the run's trace id, on the worker thread, with no authorization header. |
| An incoming trace is continued | `test_incoming_trace_is_continued_not_replaced` | The header carries the caller's trace id. |
| The API survives a tracing-aware caller | `test_request_with_traceparent_is_not_a_server_error` | No 500. |
| The real pipeline produces the tree | `test_real_run_exports_a_trace_covering_extraction_and_research` | Root `tev.run.run`, `tev.extract.*`, `tev.research.*`, one trace id, and the exported trace id equals the id stored in `execution_traces`. |

## Overhead (measured on this machine)

`scripts/otel_overhead.py`, 20,000 spans, median of 5 runs:

```
emit, telemetry off              60 ns/span
emit, telemetry on              290 ns/span
emit overhead                   230 ns/span (4.9x over the no-op branch)
20000 spans, telemetry on         5.8 ms total
flush, 100-span tree            4.10 ms (100 spans exported)
outgoing header build           788 ns/call
```

Read it as: the marginal cost of telemetry-on is **230 ns per span**, and a run
has tens to hundreds of spans, so the whole run pays well under a millisecond.
The 4.9× ratio is a ratio over a no-op branch — the absolute number is the one
that matters. `flush()` is a per-run cost (4.1 ms for 100 spans) and happens
after the verdict is already written. `headers_for_outgoing_call()` costs
~0.8 µs and runs once per Bifrost/Tavily request, not per claim.

## Not validated, and why

- **A live collector.** No OTLP collector runs in this environment. The tree,
  ids, attributes and status are validated against an in-memory exporter; the
  OTLP transport itself is the OpenTelemetry SDK's, unchanged. Verifying a real
  Jaeger/Tempo round-trip is a deployment check, not a code check, and is listed
  as an outstanding item rather than claimed.
- **SSE.** Server-sent events carry the durable `verification_events` rail, not
  spans. There is no OTel context on that path to propagate, and adding one
  would not make the rail any more authoritative. The claim that "SSE
  propagation works" would be a claim about something that does not exist.
- **Exporter retry/backoff tuning.** The SDK default is kept. A blackholed
  collector (packets dropped rather than refused) can still cost up to
  `OTEL_EXPORT_TIMEOUT_S` at flush; the run is already finished and the verdict
  already stored, but run finalisation can be delayed. Recorded, not fixed.

## Remaining risks

- `flush()` is called on the run's worker thread. With a hanging collector, run
  finalisation (not the verdict) can be delayed up to the export timeout. If
  that becomes a real deployment concern, the fix is a detached flush thread,
  which is a separate change.
- The deterministic id generator derives a 64-bit span id from the first 16 hex
  characters of a UUID. Collision probability inside one run is negligible, but
  it is a derivation, not a guarantee; a collision would merge two spans, never
  change a verdict.
- `otel.py` still reports the SDK version as a literal (`"1.45.2"`). It is a
  resource attribute used for filtering, not a dependency pin; a mismatch would
  mislabel spans in a backend, not break a run.

## Files changed in this phase

- `backend/app/verify/otel.py` — exporter rewritten around a per-run tree;
  deterministic ids; run context helpers; batch-size clamp.
- `backend/app/verify/orchestrator.py` — run-scoped exporter, attach/detach,
  `execute`/`_execute` split.
- `backend/app/main.py` — middleware header read fixed; parent context
  continued instead of replaced.
- `backend/tests/verify/test_otel_validation.py` — new: the tree, ids, durations,
  error status, privacy, collector outage, drop-on-full, two-run isolation, W3C
  propagation on a worker thread, and one end-to-end run of the real verifier.
- `backend/tests/verify/test_otel_exporter.py` — detach fix; exporter API
  unchanged otherwise.
- `scripts/otel_overhead.py` — new measurement.
