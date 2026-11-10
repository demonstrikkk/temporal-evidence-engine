-- Full-text search over the stored page text.
--
-- Why this is a column and not a join at query time
-- --------------------------------------------------
-- "Which judgments mention anticipatory bail?" had no answer. The dataset
-- search ran over `dataset_records.row_json`, which holds a record's FIELDS --
-- a title, a citation, a summary. A judgment's body, which is 20,000 to
-- 200,000 characters of the case, lives on `pages.markdown` and is cited by
-- the cells through `source.page_id`. So a search for a phrase in a
-- judgment's reasoning found nothing, and a search for the same phrase in
-- the summary found it -- which is backwards, because the summary is the
-- smaller text.
--
-- The join to find the page a cell came from is `jsonb_each(row_json)`, and
-- doing that per row, per page, per record, made the search take 28 seconds.
-- So the page id a record cites is written down once, in its own column,
-- where it can be indexed.
--
-- `page_ids` is an array of the distinct pages this record's cells cite. It
-- is a denormalisation on purpose: it is derived from `row_json`, it is
-- written in the same statement that writes the row, and it is what makes
-- "which records cite a page whose text contains X" an index scan rather
-- than a scan of every cell of every record.
--
-- Backfilled from the rows that already exist, so the corpus stored before
-- this migration is searchable too rather than silently empty.
--
-- Idempotent: re-runnable, like every migration here.

ALTER TABLE dataset_records
  ADD COLUMN IF NOT EXISTS page_ids text[] NOT NULL DEFAULT '{}';

-- The search itself: the pages whose text matches, then the records that
-- cite them. GIN on the array makes "which records cite one of these
-- pages" an index lookup.
CREATE INDEX IF NOT EXISTS dataset_records_page_ids_idx
  ON dataset_records USING GIN (page_ids);

-- The page-text side. A trigram index answers "contains this phrase"
-- without the full-text parser's word-boundary rules, which matter less
-- here than the ability to search a phrase that is not a word.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS pages_markdown_trgm_idx
  ON pages USING GIN (markdown gin_trgm_ops);

-- Backfill the page ids for the rows that predate the column.
UPDATE dataset_records dr
   SET page_ids = COALESCE(
       (SELECT array_agg(DISTINCT f.value -> 'source' ->> 'page_id')
          FROM jsonb_each(dr.row_json) f
         WHERE f.value -> 'source' ->> 'page_id' IS NOT NULL),
       '{}'::text[]);
