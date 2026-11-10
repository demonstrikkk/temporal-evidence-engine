-- PS05 - the rule version is code, so the column has no default.
--
-- 014 and 015 gave `rule_version` a default of 'tev-1.0.0'. That made the stamp
-- optional: any insert that forgot it recorded a plausible-looking version that
-- was not the version that produced the row. The app always supplies the value
-- (`config.RULE_VERSION`), and D-015 removes the setting that could override it.
-- Dropping the default turns a missing stamp into an error instead of a lie.
--
-- The column lives on `verification_runs` (014) and `claim_status_history`
-- (015); `answers` and `claims` never had one. Naming the wrong table here would
-- fail the migration, which is why the static test parses the target against
-- the CREATE TABLE definitions instead of trusting the filename.
--
-- Idempotent: DROP DEFAULT succeeds whether or not a default is present.

ALTER TABLE tev.verification_runs     ALTER COLUMN rule_version DROP DEFAULT;
ALTER TABLE tev.claim_status_history ALTER COLUMN rule_version DROP DEFAULT;

COMMENT ON COLUMN tev.verification_runs.rule_version IS
    'Rule-set identifier supplied by the application; no default, by design.';
COMMENT ON COLUMN tev.claim_status_history.rule_version IS
    'Rule-set identifier supplied by the application; no default, by design.';
