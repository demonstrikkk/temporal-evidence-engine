-- PS05 - Multi-layer persistent cache entries table
--
-- Supports L0 (request deduplication), L1 (pure deterministic computations),
-- L2 (exact model KV completions), L3 (Tavily search candidate queries),
-- L4 (extracted source snapshots), and L5 (claim-evidence candidate links).
--
-- Cache hits preserve original provenance and timestamps; replayed results
-- carry their original creation date and are never presented as live.

CREATE TABLE IF NOT EXISTS tev.cache_entries (
    cache_key TEXT PRIMARY KEY,
    cache_type TEXT NOT NULL,
    cache_version TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hit_count BIGINT NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS cache_entries_type_idx ON tev.cache_entries(cache_type);
CREATE INDEX IF NOT EXISTS cache_entries_expiry_idx ON tev.cache_entries(expires_at);
