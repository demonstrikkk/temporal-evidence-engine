-- PS05 - the run as a tree, so "what did this cost and where" is a query.
--
-- The audit rail (`verification_events`) answers "what happened, in order". It
-- does not answer "which of those things was the expensive one", because a rail
-- row has no parent, no duration and no price. These two tables are the same
-- run seen as a tree: one root span per run, one child per operation, and a
-- model call's tokens and cost attached to the span that caused them.
--
-- One writer, deliberately. `trace_spans.event_id` carries the id of the
-- `verification_events` row that describes the span, so the trace is a
-- projection of the rail rather than a second account of the same run. It is
-- text rather than bigint because the local store ids its events with a UUID and
-- the Postgres store uses a bigserial; a foreign key would force one of the two
-- adapters to lie about its own identifiers. What the column promises - that the
-- id names a real row - is enforced by `test_trace.py`, not by a constraint.
--
-- `ordinal` is the order the span was begun in. The tree has an order that does
-- not depend on wall-clock resolution or on a UUID, which is what makes a
-- replayed run reproduce the same tree.
--
-- `attempt` is *our* attempt number for the operation. A provider fallback
-- inside Bifrost is recorded on `model_calls.fallback_attempts`; it is not a
-- second span, and pretending otherwise would multiply one request into several.
--
-- `claim_id` is on the span rather than only in the event payload because the
-- question the table exists to answer - "what did this claim cost" - would
-- otherwise need a join through jsonb on the rail, which is the by-hand work
-- this work package removes.
--
-- Idempotent and additive: a re-run is a no-op.

CREATE TABLE IF NOT EXISTS tev.execution_traces (
    id               text PRIMARY KEY,
    run_id           text NOT NULL UNIQUE
                     REFERENCES tev.verification_runs(id) ON DELETE CASCADE,
    trace_id         text NOT NULL UNIQUE,
    root_span_id     text,
    status           text NOT NULL DEFAULT 'running',
    started_at       timestamptz NOT NULL DEFAULT now(),
    completed_at     timestamptz
);

COMMENT ON COLUMN tev.execution_traces.status IS
    'running | completed | partial | failed - the run status mirrored at close.';

CREATE INDEX IF NOT EXISTS execution_traces_run_idx
    ON tev.execution_traces (run_id);

CREATE TABLE IF NOT EXISTS tev.trace_spans (
    id                text PRIMARY KEY,
    trace_id          text NOT NULL
                      REFERENCES tev.execution_traces(trace_id) ON DELETE CASCADE,
    parent_span_id    text,
    event_id          text,
    claim_id          text NOT NULL DEFAULT '',
    ordinal           integer NOT NULL DEFAULT 0,
    stage             text NOT NULL DEFAULT '',
    operation         text NOT NULL DEFAULT '',
    attempt           integer NOT NULL DEFAULT 1,
    requested_model   text NOT NULL DEFAULT '',
    resolved_model    text NOT NULL DEFAULT '',
    provider          text NOT NULL DEFAULT '',
    prompt_version    text NOT NULL DEFAULT '',
    prompt_hash       text NOT NULL DEFAULT '',
    input_hash        text NOT NULL DEFAULT '',
    output_hash       text NOT NULL DEFAULT '',
    tool_name         text NOT NULL DEFAULT '',
    tool_arguments    jsonb NOT NULL DEFAULT '{}'::jsonb,
    tool_result_ref   text NOT NULL DEFAULT '',
    input_tokens      integer NOT NULL DEFAULT 0,
    output_tokens     integer NOT NULL DEFAULT 0,
    cost_usd          double precision NOT NULL DEFAULT 0,
    cost_source       text NOT NULL DEFAULT 'none',
    latency_ms        integer NOT NULL DEFAULT 0,
    cache_status      text NOT NULL DEFAULT '',
    error_type        text NOT NULL DEFAULT '',
    decision_rule     text NOT NULL DEFAULT '',
    decision_result   text NOT NULL DEFAULT '',
    started_at        timestamptz NOT NULL DEFAULT now(),
    ended_at          timestamptz
);

COMMENT ON TABLE tev.trace_spans IS
    'One operation of one run: a model call, a tool call, a gate, or the run itself.';
COMMENT ON COLUMN tev.trace_spans.event_id IS
    'The verification_events row this span is reported by; NULL only if the rail has no row for it.';
COMMENT ON COLUMN tev.trace_spans.cost_source IS
    'gateway | catalogue | none - the same vocabulary as model_calls.cost_source.';
COMMENT ON COLUMN tev.trace_spans.cache_status IS
    'live | snapshot | cached - whether the work behind this span was done now or replayed.';

CREATE INDEX IF NOT EXISTS trace_spans_trace_idx
    ON tev.trace_spans (trace_id, ordinal);
CREATE INDEX IF NOT EXISTS trace_spans_parent_idx
    ON tev.trace_spans (trace_id, parent_span_id)
    WHERE parent_span_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS trace_spans_event_idx
    ON tev.trace_spans (event_id)
    WHERE event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS trace_spans_stage_idx
    ON tev.trace_spans (trace_id, stage);
