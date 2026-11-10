-- PS05 - Quota ledger table for persistent rate and budget accounting
--
-- Tracks external API usage across providers (Groq, Tavily, Bifrost)
-- and windows (minute, day, month) to prevent quota exhaustion before it happens.

CREATE TABLE IF NOT EXISTS tev.quota_ledger (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    window_type TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    used_amount BIGINT NOT NULL DEFAULT 0,
    allocated_limit BIGINT NOT NULL,
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS quota_ledger_window_idx
ON tev.quota_ledger(provider, resource_type, window_type, window_start);
