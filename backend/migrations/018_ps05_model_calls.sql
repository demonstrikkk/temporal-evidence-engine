-- PS05 — model calls and research cache.
--
-- The cache is a guarantee, not an optimisation: `CACHE_MODE=read-only` replays
-- a recorded run with no network, and every replayed result is labelled
-- `snapshot` with its original timestamp.
--
-- `cacheable` separates the two jobs of this table. A successful call occupies
-- the unique cache key; a failed call is an audit row that must not poison the
-- key, so the partial unique index ignores it.

CREATE TABLE IF NOT EXISTS tev.model_calls (
    id                text PRIMARY KEY,
    run_id            text REFERENCES tev.verification_runs(id) ON DELETE CASCADE,
    claim_id          text REFERENCES tev.claims(id) ON DELETE SET NULL,
    stage             text NOT NULL,
    provider          text NOT NULL DEFAULT '',
    model             text NOT NULL DEFAULT '',
    bifrost_request_id text NOT NULL DEFAULT '',
    prompt_hash       text NOT NULL,
    response_json     jsonb,
    input_tokens      integer NOT NULL DEFAULT 0,
    output_tokens     integer NOT NULL DEFAULT 0,
    cost_usd          numeric(12, 6) NOT NULL DEFAULT 0,
    latency_ms        integer NOT NULL DEFAULT 0,
    fallback_attempts integer NOT NULL DEFAULT 0,
    status            text NOT NULL DEFAULT 'ok',
    cacheable         boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS model_calls_cache_idx
    ON tev.model_calls (prompt_hash, model, stage) WHERE cacheable;

CREATE INDEX IF NOT EXISTS model_calls_run_idx ON tev.model_calls (run_id);

CREATE TABLE IF NOT EXISTS tev.research_cache (
    cache_key  text PRIMARY KEY,
    tool       text NOT NULL,
    payload    jsonb NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS research_cache_tool_idx ON tev.research_cache (tool);
