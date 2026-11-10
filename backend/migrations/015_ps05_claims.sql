-- PS05 — claims.
--
-- Four timestamps do not live here. This table holds the claim and its verdict;
-- `generated_at` is on answers, `published_date` on source_revisions,
-- `event_date` on evidence_spans, `retrieved_at` on source_revisions.

CREATE TABLE IF NOT EXISTS tev.claims (
    id                    text PRIMARY KEY,
    answer_id             text NOT NULL REFERENCES tev.answers(id) ON DELETE CASCADE,
    run_id                text NOT NULL REFERENCES tev.verification_runs(id) ON DELETE CASCADE,
    ordinal               integer NOT NULL DEFAULT 0,
    claim_text            text NOT NULL,
    span_start            integer NOT NULL DEFAULT -1,
    span_end              integer NOT NULL DEFAULT -1,
    subject               text NOT NULL DEFAULT '',
    predicate             text NOT NULL DEFAULT '',
    object_raw            text NOT NULL DEFAULT '',
    object_normalized     jsonb NOT NULL DEFAULT '{}'::jsonb,
    qualifiers            jsonb NOT NULL DEFAULT '{}'::jsonb,
    claim_type            text NOT NULL DEFAULT 'factual',
    time_sensitive        boolean NOT NULL DEFAULT false,
    volatility_class      text NOT NULL DEFAULT 'moderate',
    temporal_scope        jsonb NOT NULL DEFAULT '{}'::jsonb,
    extraction_confidence double precision NOT NULL DEFAULT 0,
    status                text NOT NULL DEFAULT 'pending',
    reason_code           text,
    next_check_at         timestamptz,
    last_verified_at      timestamptz
);

CREATE INDEX IF NOT EXISTS claims_run_idx ON tev.claims (run_id, ordinal);
CREATE INDEX IF NOT EXISTS claims_due_idx ON tev.claims (next_check_at)
    WHERE next_check_at IS NOT NULL;

-- Append-only. A status change is an event, not an update of history.
CREATE TABLE IF NOT EXISTS tev.claim_status_history (
    id              text PRIMARY KEY,
    claim_id        text NOT NULL REFERENCES tev.claims(id) ON DELETE CASCADE,
    run_id          text NOT NULL REFERENCES tev.verification_runs(id) ON DELETE CASCADE,
    previous_status text NOT NULL DEFAULT 'pending',
    new_status      text NOT NULL,
    reason_code     text,
    rule_version    text NOT NULL DEFAULT 'tev-1.0.0',
    decided_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS claim_status_history_claim_idx
    ON tev.claim_status_history (claim_id, decided_at);

-- The claim's value over time. `valid_from` is the event date, never the
-- publication date; NULL means unknown and is not interpreted as "now".
CREATE TABLE IF NOT EXISTS tev.claim_assertions (
    id               text PRIMARY KEY,
    claim_id         text NOT NULL REFERENCES tev.claims(id) ON DELETE CASCADE,
    value_raw        text NOT NULL DEFAULT '',
    value_normalized jsonb NOT NULL DEFAULT '{}'::jsonb,
    valid_from       timestamptz,
    valid_until      timestamptz,
    evidence_id      text,
    is_original      boolean NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS claim_assertions_claim_idx
    ON tev.claim_assertions (claim_id);
