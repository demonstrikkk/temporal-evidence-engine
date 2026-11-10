-- Record which domain edition produced a dataset.
--
-- Why this is a column and not a lookup
-- ------------------------------------
-- "Which dataset is the legal one?" was answerable only by reading every
-- dataset's name, or by already knowing its id. The edition that produced a
-- dataset is on the run's plan, and the plan lives in `workflows.plan_json` --
-- so answering the question meant a join per dataset, or a plan read per
-- dataset, at ~170ms a statement against a remote pooler. Thirty-two datasets
-- is five seconds of waiting to filter a list.
--
-- So the fact is stamped on the row when the run finalizes, the same way
-- `name` and `record_count` are. The column is what makes
-- `GET /api/datasets?edition=datagoblin_legal` a filter rather than a scan.
--
-- The value is the edition's *name*, copied from the plan, so the row stays
-- readable on its own: a dataset row says which vertical produced it without
-- anyone having to resolve a plan to find out.
--
-- Default '' because a dataset created by a live run with no edition in play
-- is the common case, and an empty string is the honest answer for it. The
-- column is NOT NULL so a dataset cannot be "unknown" by accident.
--
-- Existing rows are stamped from their own plan, so the filter is correct for
-- data stored before this migration rather than silently empty for it.
--
-- Idempotent: re-runnable, like every migration here.

ALTER TABLE datasets ADD COLUMN IF NOT EXISTS edition text NOT NULL DEFAULT '';

-- Backfill from the plan, for rows that predate the column. The join is
-- datasets -> runs -> workflows, which is the same path the run itself uses.
UPDATE datasets d
   SET edition = COALESCE(w.plan_json ->> 'edition', '')
  FROM runs r
  LEFT JOIN workflows w ON w.id = r.workflow_id
 WHERE d.edition = ''
   AND r.id = d.run_id;

CREATE INDEX IF NOT EXISTS datasets_edition_idx ON datasets(edition);
