-- PS05 — evidence spans and the proposal/settlement link.
--
-- `evidence_spans` is the locatable quote and its three dates: the event date
-- (when the fact changed), and — through the revision — the publication and
-- retrieval dates. `claim_evidence` records what the model proposed and what
-- settlement accepted. The two are separate columns on purpose: a reviewer can
-- see the model was overruled.

CREATE TABLE IF NOT EXISTS tev.evidence_spans (
    id                 text PRIMARY KEY,
    claim_id           text NOT NULL REFERENCES tev.claims(id) ON DELETE CASCADE,
    source_revision_id text NOT NULL REFERENCES tev.source_revisions(id) ON DELETE CASCADE,
    quote_text         text NOT NULL,
    start_char         integer NOT NULL DEFAULT -1,
    end_char           integer NOT NULL DEFAULT -1,
    event_date         date,
    event_date_quote   text,
    event_date_status  text NOT NULL DEFAULT 'missing',
    retrieved_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS evidence_spans_claim_idx
    ON tev.evidence_spans (claim_id);

CREATE TABLE IF NOT EXISTS tev.claim_evidence (
    id                        text PRIMARY KEY,
    claim_id                  text NOT NULL REFERENCES tev.claims(id) ON DELETE CASCADE,
    evidence_id               text NOT NULL REFERENCES tev.evidence_spans(id) ON DELETE CASCADE,
    relationship_proposed     text NOT NULL DEFAULT 'silent',
    relationship_settled      text,
    settlement_status         text,
    settlement_reason         text,
    settlement_start          integer,
    settlement_end            integer,
    candidate_value_raw       text,
    candidate_value_normalized jsonb,
    model_call_id             text,
    created_at                timestamptz NOT NULL DEFAULT now(),
    UNIQUE (claim_id, evidence_id)
);

CREATE INDEX IF NOT EXISTS claim_evidence_claim_idx
    ON tev.claim_evidence (claim_id);
