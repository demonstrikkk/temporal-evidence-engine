-- PS05 - what the answer asserted, and what the run actually covered.
--
-- The extractor is allowed to return fewer claims than the answer contains. It
-- is not allowed to make that invisible. `assertion_spans` is the durable
-- record: one row per checkable value found by the deterministic scanner, with
-- the claim that covers it (or NULL) and a disposition. `coverage_audits` is the
-- per-run summary the report and the UI read.
--
-- A coverage disposition is not a verdict. `claims.status` keeps its four
-- values plus pending; nothing here may write a status, and nothing here may
-- promote a cell. In particular there is no `verified` column: the promotion
-- chokepoint stays `validator.settle`.
--
-- `claims.extracted_by` records which pass produced a claim. `extractor` is the
-- first pass; `coverage_repair` is the one focused re-extraction the audit is
-- allowed to request. The default keeps every existing row reading as the first
-- pass, which is what it was.
--
-- Idempotent and additive: a re-run is a no-op.

ALTER TABLE tev.claims
    ADD COLUMN IF NOT EXISTS extracted_by text NOT NULL DEFAULT 'extractor';

COMMENT ON COLUMN tev.claims.extracted_by IS
    'extractor | coverage_repair - which extraction pass produced the claim.';

CREATE TABLE IF NOT EXISTS tev.assertion_spans (
    id              text PRIMARY KEY,
    run_id          text NOT NULL REFERENCES tev.verification_runs(id) ON DELETE CASCADE,
    answer_id       text NOT NULL REFERENCES tev.answers(id) ON DELETE CASCADE,
    claim_id        text REFERENCES tev.claims(id) ON DELETE CASCADE,
    kind            text NOT NULL DEFAULT 'number',
    text            text NOT NULL DEFAULT '',
    start_char      integer NOT NULL DEFAULT 0,
    end_char        integer NOT NULL DEFAULT 0,
    sentence_start  integer NOT NULL DEFAULT 0,
    sentence_end    integer NOT NULL DEFAULT 0,
    disposition     text NOT NULL DEFAULT 'not_checked',
    signals         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS assertion_spans_run_idx
    ON tev.assertion_spans (run_id, start_char);
CREATE INDEX IF NOT EXISTS assertion_spans_claim_idx
    ON tev.assertion_spans (claim_id)
    WHERE claim_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS assertion_spans_disposition_idx
    ON tev.assertion_spans (run_id, disposition);

CREATE TABLE IF NOT EXISTS tev.coverage_audits (
    run_id              text PRIMARY KEY REFERENCES tev.verification_runs(id) ON DELETE CASCADE,
    answer_id           text NOT NULL REFERENCES tev.answers(id) ON DELETE CASCADE,
    answer_chars        integer NOT NULL DEFAULT 0,
    sentences           integer NOT NULL DEFAULT 0,
    candidates          integer NOT NULL DEFAULT 0,
    claims              integer NOT NULL DEFAULT 0,
    claims_without_candidate integer NOT NULL DEFAULT 0,
    total_assertions    integer NOT NULL DEFAULT 0,
    checked             integer NOT NULL DEFAULT 0,
    coverage            double precision NOT NULL DEFAULT 0,
    material_assertions integer NOT NULL DEFAULT 0,
    material_coverage   double precision NOT NULL DEFAULT 0,
    dispositions        jsonb NOT NULL DEFAULT '{}'::jsonb,
    repair_calls        integer NOT NULL DEFAULT 0,
    repaired_claims     integer NOT NULL DEFAULT 0,
    repair_targets      jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE tev.coverage_audits IS
    'One row per run: how many assertions the answer contained, how many a claim covered, and why the rest were not checked.';
