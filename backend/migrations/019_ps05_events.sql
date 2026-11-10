-- PS05 — durable run events.
--
-- Sequence is assigned by the insert itself (MAX + 1 inside a transaction), not
-- by the caller, so two writers cannot collide on the same number. The SSE
-- stream and the polling endpoint read the same rows; the UI cannot show a
-- stage the store does not have.
--
-- `id` is a bigserial because events are the one high-volume table here and a
-- monotonic integer is what makes `sequence` cheap to assign.

CREATE TABLE IF NOT EXISTS tev.verification_events (
    id         bigserial PRIMARY KEY,
    run_id     text NOT NULL REFERENCES tev.verification_runs(id) ON DELETE CASCADE,
    sequence   integer NOT NULL,
    event_type text NOT NULL,
    payload    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, sequence)
);

CREATE INDEX IF NOT EXISTS verification_events_run_idx
    ON tev.verification_events (run_id, sequence);
