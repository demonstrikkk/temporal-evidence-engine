-- PS05 - make the deterministic audit's blind spot visible.
--
-- The audit finds values: money, percentages, measures, dates, versions and
-- bare numbers. A sentence that asserts a property, a location, a relation or a
-- comparison produces no candidate, so it never enters the denominator. When
-- such a sentence also produced no claim, `coverage_audits.coverage` reports
-- 100% - a complete-looking number for an answer the audit could not read at
-- all.
--
-- `unscanned` and `unscanned_sentences` record those sentences without changing
-- `coverage` or `material_coverage`: this is a disclosure, not a rule change.
-- `claims.status` is untouched, no verdict is affected, and nothing here may
-- promote a cell.
--
-- Idempotent and additive: a re-run is a no-op and every existing row reads as
-- "the audit did not record this", which is what it did.

ALTER TABLE tev.coverage_audits
    ADD COLUMN IF NOT EXISTS unscanned integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS unscanned_sentences jsonb NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN tev.coverage_audits.unscanned IS
    'Sentences with no checkable value and no stored claim: the deterministic scanner could not read them. Informational; does not change coverage.';
COMMENT ON COLUMN tev.coverage_audits.unscanned_sentences IS
    'The unscanned sentences with their offsets and signals, so a reader can see what the ratio excludes.';
