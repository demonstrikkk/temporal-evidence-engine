-- PS05 - say what the two evidence columns mean, in the database itself.
--
-- `relationship_settled` used to receive 'settled'/'unsettled' and
-- `settlement_status` 'settled'/'not_settled'. That put a settlement word in a
-- relationship column, and put the same word in both, so `relationship_settled`
-- could not answer the only question its name asks: which relationship survived
-- the gate. A reader querying the schema's own vocabulary
-- (`relationship_settled = 'contradicts'`) matched nothing.
--
-- The writer now stores:
--   relationship_settled  supports | contradicts | mentions_only | silent, NULL
--                         when the gate rejected the proposal
--   settlement_status     accepted | rejected
--
-- 'verified' is deliberately absent. In this codebase that word names the
-- promotion chokepoint on a cell (`verification_status`), not the fate of a
-- proposal, and reusing it here would blur the one distinction the chokepoint
-- test exists to protect.
--
-- Comments only: no rows are rewritten. Historical rows keep what they recorded,
-- because rewriting an audit trail to match a later vocabulary is how a record
-- stops being evidence of anything.

COMMENT ON COLUMN tev.claim_evidence.relationship_settled IS
    'Relationship that survived settlement: supports|contradicts|mentions_only|silent, NULL when rejected.';

COMMENT ON COLUMN tev.claim_evidence.settlement_status IS
    'Whether the settlement gate accepted this proposal: accepted|rejected.';

COMMENT ON COLUMN tev.claim_evidence.relationship_proposed IS
    'What the model proposed: supports|contradicts|mentions_only|silent. Never a verdict.';
