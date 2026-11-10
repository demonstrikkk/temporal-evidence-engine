-- PS05 — what the gateway answered, as distinct from what was asked for.
--
-- `model` is the cache key: the model string the caller requested, which is what
-- `model_calls_cache_idx` is built on and what makes a replay deterministic.
-- When Bifrost resolves the request to a different model — most often because
-- the primary provider failed and a fallback answered — storing only the
-- resolved name under `model` made the cache key depend on a runtime outcome,
-- so a later identical request never matched and the "caching is a guarantee"
-- contract quietly held only when no fallback ever fired.
--
-- So the two facts get two columns: `model` (requested, part of the key) and
-- `resolved_model` (what actually answered). The panel reads the resolved one,
-- because "groq/openai/gpt-oss-20b" is not an honest label for a response that
-- came back from ollama.

ALTER TABLE tev.model_calls
    ADD COLUMN IF NOT EXISTS resolved_model text NOT NULL DEFAULT '';

COMMENT ON COLUMN tev.model_calls.model IS
    'Model requested by the caller; part of the replay cache key.';
COMMENT ON COLUMN tev.model_calls.resolved_model IS
    'Model the gateway actually used, after routing and fallback.';
