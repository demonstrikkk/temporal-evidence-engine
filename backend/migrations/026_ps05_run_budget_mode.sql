-- PS05 — the research budget mode a run was created under.
--
-- Budget mode changes how much research may be purchased (query count, live
-- search allowed), never the evidence threshold for a verdict. It is stored on
-- the run so a replay and an audit can see why a claim abstained.

ALTER TABLE tev.verification_runs
    ADD COLUMN IF NOT EXISTS budget_mode text NOT NULL DEFAULT 'balanced';
