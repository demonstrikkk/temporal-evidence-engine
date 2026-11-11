-- PS05 schema housekeeping: evaluation, model profiles, provider connections,
-- and usage tables, all in schema `tev`.
--
-- These tables are additive: every column has a default, every file is
-- idempotent (CREATE TABLE IF NOT EXISTS), and they are applied in filename
-- order by scripts/db_migrate.py. If you write a column, something must read it
-- (WP-4 reads evaluation_cases/evaluation_results; WP-6 reads
-- provider_connections/model_profiles/user_usage; WP-9 reads all of them).
--
-- `provider_connections.key_crypt` holds a Fernet-encrypted provider key. The
-- encryption key comes from the environment (TEV_FERNET_KEY); losing the env
-- loses the connections. Documented, acceptable for BYOK v1.
-- `key_last4` is never secret; it is the only key material the API may return.

CREATE TABLE IF NOT EXISTS tev.evaluation_runs (
    id             text PRIMARY KEY,
    name           text NOT NULL DEFAULT '',
    model          text NOT NULL DEFAULT '',
    prompt_version text NOT NULL DEFAULT '',
    rule_version   text NOT NULL DEFAULT '',
    status         text NOT NULL DEFAULT 'running',
    created_at     timestamptz NOT NULL DEFAULT now(),
    completed_at   timestamptz
);

COMMENT ON TABLE tev.evaluation_runs IS 'One model-vs-model or suite run: the versions under test and the outcome.';
COMMENT ON COLUMN tev.evaluation_runs.prompt_version IS 'Registered prompt version (D-026), not a free-form label.';
COMMENT ON COLUMN tev.evaluation_runs.rule_version IS 'RULE_VERSION in force for this run.';

CREATE TABLE IF NOT EXISTS tev.evaluation_cases (
    id              text PRIMARY KEY,
    prompt          text NOT NULL DEFAULT '',
    answer          text NOT NULL DEFAULT '',
    expected_status text NOT NULL DEFAULT 'unverifiable',
    expected_quote  text NOT NULL DEFAULT '',
    expected_event_date date,
    gold_labels     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE tev.evaluation_cases IS 'Labelled cases: expected verdict, quote, and event date per case.';
COMMENT ON COLUMN tev.evaluation_cases.gold_labels IS 'Expected assertion set, coverage dispositions, and other per-case labels (WP-4).';

CREATE TABLE IF NOT EXISTS tev.evaluation_results (
    id                text PRIMARY KEY,
    evaluation_run_id text NOT NULL
                      REFERENCES tev.evaluation_runs(id) ON DELETE CASCADE,
    evaluation_case_id text NOT NULL
                       REFERENCES tev.evaluation_cases(id) ON DELETE CASCADE,
    model             text NOT NULL DEFAULT '',
    prompt_version    text NOT NULL DEFAULT '',
    rule_version      text NOT NULL DEFAULT '',
    status            text NOT NULL DEFAULT '',
    correct           boolean NOT NULL DEFAULT false,
    cost_usd          numeric NOT NULL DEFAULT 0,
    latency_ms        integer NOT NULL DEFAULT 0,
    created_at        timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE tev.evaluation_results IS 'One case result: model, prompt, rule versions stored per result so runs stay comparable.';
COMMENT ON COLUMN tev.evaluation_results.correct IS 'Whether the produced verdict matched expected_status.';

CREATE TABLE IF NOT EXISTS tev.model_profiles (
    id             text PRIMARY KEY,
    name           text NOT NULL DEFAULT '',
    stage          text NOT NULL DEFAULT '',
    model          text NOT NULL DEFAULT '',
    prompt_version text NOT NULL DEFAULT '',
    is_default     boolean NOT NULL DEFAULT false,
    created_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE tev.model_profiles IS 'Per-stage model selection as data, not env (WP-6 / WP-9).';
COMMENT ON COLUMN tev.model_profiles.stage IS 'extract | propose | settle | decide | repair | synthesize.';

CREATE TABLE IF NOT EXISTS tev.provider_connections (
    id           text PRIMARY KEY,
    kind         text NOT NULL DEFAULT 'model',
    provider     text NOT NULL DEFAULT '',
    label        text NOT NULL DEFAULT '',
    key_crypt    text NOT NULL DEFAULT '',
    key_last4    text NOT NULL DEFAULT '',
    status       text NOT NULL DEFAULT 'pending',
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    last_error   text
);

COMMENT ON TABLE tev.provider_connections IS 'One credential per provider: encrypted at rest, never returned by the API, never logged (D-027/D-026).';
COMMENT ON COLUMN tev.provider_connections.kind IS 'model | research.';
COMMENT ON COLUMN tev.provider_connections.key_crypt IS 'Fernet-encrypted provider key; the API never returns this column.';
COMMENT ON COLUMN tev.provider_connections.key_last4 IS 'Last four characters of the provider key, for UI identification only.';

CREATE TABLE IF NOT EXISTS tev.user_usage (
    id          text PRIMARY KEY,
    user_id     text NOT NULL DEFAULT '',
    model_calls integer NOT NULL DEFAULT 0,
    searches    integer NOT NULL DEFAULT 0,
    cost_usd    numeric NOT NULL DEFAULT 0,
    period_start timestamptz NOT NULL DEFAULT now(),
    period_end   timestamptz NOT NULL DEFAULT now(),
    created_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE tev.user_usage IS 'Per-user usage rollup for BYOK mode (WP-6).';

CREATE INDEX IF NOT EXISTS evaluation_results_run_idx ON tev.evaluation_results (evaluation_run_id);
CREATE INDEX IF NOT EXISTS evaluation_results_case_idx ON tev.evaluation_results (evaluation_case_id);
CREATE INDEX IF NOT EXISTS model_profiles_stage_idx ON tev.model_profiles (stage);
CREATE INDEX IF NOT EXISTS provider_connections_kind_idx ON tev.provider_connections (kind);
CREATE INDEX IF NOT EXISTS user_usage_user_idx ON tev.user_usage (user_id);
