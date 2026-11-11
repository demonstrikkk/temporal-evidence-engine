-- PS05 - one research plan per subject group, executed once.
--
-- The per-claim path paid for the same query and the same page again for every
-- claim that shared a subject: a five-claim answer about one product issued
-- five searches for "the product's price" and extracted the pricing page five
-- times. The plan is the row that says which questions were asked once, for
-- which claims, and what the pre-filter then skipped.
--
-- `claim_ids` is jsonb because a plan is one row per group and the group is
-- read as a whole; a join table would add a second place for the group to
-- disagree with itself. `queries` is the deduplicated list actually offered to
-- the search engine, in the order it was asked, so a replay can be compared
-- query by query.
--
-- `pairs_examined` / `pairs_skipped` are the disclosure the pre-filter needs:
-- a cheaper run that hides what it did not look at is the failure this
-- repository exists to remove, so the count of skipped (claim, evidence) pairs
-- is stored beside the count of judged ones rather than inferred later from
-- model_calls, which cannot say what was never sent.
--
-- Idempotent and additive: a re-run is a no-op.

CREATE TABLE IF NOT EXISTS tev.research_plans (
    id              text PRIMARY KEY,
    run_id          text NOT NULL
                    REFERENCES tev.verification_runs(id) ON DELETE CASCADE,
    group_key       text NOT NULL DEFAULT '',
    claim_ids       jsonb NOT NULL DEFAULT '[]'::jsonb,
    queries         jsonb NOT NULL DEFAULT '[]'::jsonb,
    queries_asked   jsonb NOT NULL DEFAULT '[]'::jsonb,
    budget_mode     text NOT NULL DEFAULT 'balanced',
    queries_dropped integer NOT NULL DEFAULT 0,
    pairs_examined  integer NOT NULL DEFAULT 0,
    pairs_skipped   integer NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE tev.research_plans IS
    'One row per subject group: the queries asked once on behalf of every claim in the group.';
COMMENT ON COLUMN tev.research_plans.group_key IS
    'The normalised claim subject the group was formed on; empty for claims with no usable subject.';
COMMENT ON COLUMN tev.research_plans.queries IS
    'The queries the plan intended to ask, in order, after within-group deduplication.';
COMMENT ON COLUMN tev.research_plans.queries_asked IS
    'The queries actually sent. Shorter than `queries` when sufficiency or a failure stopped the plan.';
COMMENT ON COLUMN tev.research_plans.queries_dropped IS
    'Queries the budget mode refused. Non-zero means the plan was truncated, and the run says so.';
COMMENT ON COLUMN tev.research_plans.pairs_examined IS
    'Claim/evidence pairs sent to the relationship judge (or settled from a stored revision).';
COMMENT ON COLUMN tev.research_plans.pairs_skipped IS
    'Claim/evidence pairs the deterministic pre-filter refused, with no model call made.';

CREATE INDEX IF NOT EXISTS research_plans_run_idx
    ON tev.research_plans (run_id);
CREATE INDEX IF NOT EXISTS research_plans_group_idx
    ON tev.research_plans (run_id, group_key);
