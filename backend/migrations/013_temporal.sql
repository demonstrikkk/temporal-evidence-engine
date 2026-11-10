-- 013_temporal.sql — the time a request asked about, and the time a page says
-- its content is from.
--
-- Two different facts, deliberately in two places:
--
--   datasets.temporal_json   what the prompt asked for (a point or a window)
--   pages.published_date     when the stored page said its content was written
--
-- A cell's `valid_time` is copied from its page's `published_date`, so an
-- evidence record can carry the date its fact was true without a second query.
-- Neither column is allowed to influence `verification_status`: a quote either
-- locates on the stored text or it does not, and a date mismatch is a relevance
-- finding, not a proof failure.
--
-- Additive and re-runnable. Both columns have defaults so old rows and old
-- writers keep working.

ALTER TABLE datasets
  ADD COLUMN IF NOT EXISTS temporal_json jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE pages
  ADD COLUMN IF NOT EXISTS published_date text NOT NULL DEFAULT '';

-- Finding "datasets that asked about a particular window" and "pages we know the
-- date of" should not scan the whole table once either matters. Partial indexes
-- keep the empty default out.
CREATE INDEX IF NOT EXISTS datasets_temporal_kind_idx
  ON datasets ((temporal_json ->> 'kind'));

CREATE INDEX IF NOT EXISTS pages_published_date_idx
  ON pages (published_date)
  WHERE published_date <> '';
