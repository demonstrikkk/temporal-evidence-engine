-- PS05 / Temporal Evidence Engine — schema, answers, runs.
--
-- Everything the verifier owns lives in schema `tev`. The seeded database also
-- holds the DataGoblin tables in `public`; a separate schema means this product
-- can be dropped with `DROP SCHEMA tev CASCADE` without touching them, and
-- nothing here can shadow an upstream table name.
--
-- Idempotent and additive by policy: every statement is IF NOT EXISTS, and every
-- column added later carries a default.

CREATE SCHEMA IF NOT EXISTS tev;

CREATE TABLE IF NOT EXISTS tev.answers (
    id                  text PRIMARY KEY,
    prompt              text NOT NULL DEFAULT '',
    answer_text         text NOT NULL,
    generated_at        timestamptz,
    generated_at_source text NOT NULL DEFAULT 'unknown',
    submitted_at        timestamptz NOT NULL DEFAULT now(),
    locale              text NOT NULL DEFAULT 'en',
    source_urls         jsonb NOT NULL DEFAULT '[]'::jsonb,
    domain_filter       jsonb NOT NULL DEFAULT '[]'::jsonb,
    skip_non_factual    boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS tev.verification_runs (
    id           text PRIMARY KEY,
    answer_id    text NOT NULL REFERENCES tev.answers(id) ON DELETE CASCADE,
    status       text NOT NULL DEFAULT 'queued',
    stage        text NOT NULL DEFAULT 'queued',
    rule_version text NOT NULL DEFAULT 'tev-1.0.0',
    cache_mode   text NOT NULL DEFAULT 'read-write',
    started_at   timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    error        jsonb
);

CREATE INDEX IF NOT EXISTS verification_runs_started_idx
    ON tev.verification_runs (started_at DESC);

CREATE INDEX IF NOT EXISTS verification_runs_status_idx
    ON tev.verification_runs (status);
