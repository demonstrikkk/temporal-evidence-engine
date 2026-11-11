-- PS05 - where a call's `cost_usd` came from.
--
-- Bifrost answers a Mistral completion with `cost: null` and then publishes that
-- model's prices in `GET /v1/models` (`pricing.prompt`, `pricing.completion`).
-- Reporting the null as 0.00 said a run was free when it had spent money, so the
-- client now prices such a call from the catalogue. The amount alone cannot say
-- which of the two happened, and the difference matters: a bill and an estimate
-- are not the same claim.
--
--   gateway    the completion reported the cost
--   catalogue  priced from the gateway's own published prices
--   none       unpriced; `cost_usd` is 0.00 because nothing was known
--
-- The default keeps every row written before this column reading as `gateway`,
-- which is exactly what those rows meant.
--
-- Idempotent and additive: a re-run is a no-op.

ALTER TABLE tev.model_calls
    ADD COLUMN IF NOT EXISTS cost_source text NOT NULL DEFAULT 'gateway';

COMMENT ON COLUMN tev.model_calls.cost_source IS
    'gateway | catalogue | none - how cost_usd was obtained.';
