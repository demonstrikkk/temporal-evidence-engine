-- PS05 — sources and their append-only revisions.
--
-- `content` is the FULL extracted text. Evidence offsets index into it, so it is
-- never truncated, rewritten, or updated in place. New bytes get a new row;
-- identical bytes reuse one.

CREATE TABLE IF NOT EXISTS tev.sources (
    id            text PRIMARY KEY,
    canonical_url text NOT NULL UNIQUE,
    domain        text NOT NULL DEFAULT '',
    title         text NOT NULL DEFAULT '',
    first_seen_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS sources_domain_idx ON tev.sources (domain);

CREATE TABLE IF NOT EXISTS tev.source_revisions (
    id                    text PRIMARY KEY,
    source_id             text NOT NULL REFERENCES tev.sources(id) ON DELETE CASCADE,
    content_hash          text NOT NULL,
    content               text NOT NULL,
    published_date        date,
    published_date_source text NOT NULL DEFAULT 'unknown',
    retrieved_at          timestamptz NOT NULL DEFAULT now(),
    extraction_method     text NOT NULL DEFAULT 'tavily_extract',
    tavily_request_id     text NOT NULL DEFAULT '',
    fetch_outcome         jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (source_id, content_hash)
);

CREATE INDEX IF NOT EXISTS source_revisions_source_idx
    ON tev.source_revisions (source_id);
