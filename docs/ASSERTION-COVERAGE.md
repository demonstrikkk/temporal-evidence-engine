# Assertion coverage: what the deterministic audit can and cannot see

**Baseline:** `60843c1` (WP-10) plus the Phase-1 instrumentation fixes recorded in
`docs/WP10-VALIDATION.md`.
**Scope:** investigation only. No extraction prompt changed, no model call added,
no verdict rule touched, no product code changed.
**Reproduce:**

```powershell
backend\.venv\Scripts\python.exe scripts\coverage_probe.py
backend\.venv\Scripts\python.exe scripts\coverage_probe.py --json
```

Inputs: `eval/coverage_taxonomy.jsonl` (13 human-reviewed cases, one per assertion
shape) and `eval/assertion_gold.jsonl` (the existing 11-case extraction gold set).
The probe imports `app.verify.assertions`, `app.verify.segment` and
`app.verify.coverage`; it makes no network call and touches no database.

## Why this investigation exists

D-021 recorded the original failure: the Eiffel paragraph produced two claims
from roughly seven assertions and nothing said the other five were skipped. The
fix was a deterministic audit (`coverage.py`) driven by a value scanner
(`assertions.py`). The existing extraction suite reports **100% recall** against
`eval/assertion_gold.jsonl`.

That number is true and it is circular. Every expected assertion in the gold set
is a value, and the scanner is a set of regexes for exactly those values:

| gold kind | expected assertions |
|-----------|---------------------|
| money     | 6 |
| measure   | 3 |
| number    | 3 |
| date      | 2 |
| percent   | 2 |
| version   | 2 |
| **total** | **18 (100% in the scanner's vocabulary)** |

The question this probe asks is different: over assertion shapes the scanner was
*not* built for, how much of what a reader would check does the audit see?

## Result

| case | category | human assertions | scanner found | blind material | audit coverage (no claims) | honest coverage |
|------|----------|-----------------:|--------------:|---------------:|---------------------------:|----------------:|
| tax-01 | short-factual | 1 | 1 | 0 | 0.0% | 100.0% |
| tax-02 | long-paragraph-many-facts | 9 | 2 | 7 | 0.0% | 22.2% |
| tax-03 | multiple-dates-one-sentence | 2 | 2 | 0 | 0.0% | 100.0% |
| tax-04 | comparison-ranking | 1 | 0 | 1 | **100.0%** | 0.0% |
| tax-05 | historical-superlative | 2 | 1 | 1 | 0.0% | 50.0% |
| tax-06 | measurement-units | 1 | 1 | 0 | 0.0% | 100.0% |
| tax-07 | several-entities | 2 | 1 | 1 | 0.0% | 50.0% |
| tax-08 | subjective-opinion | 1 | 0 | 0 | 100.0% | 0.0% |
| tax-09 | prediction-future | 1 | 1 | 0 | 0.0% | 100.0% |
| tax-10 | ambiguous-unverifiable | 1 | 0 | 0 | 100.0% | 0.0% |
| tax-11 | non-numeric-facts | 2 | 0 | 2 | **100.0%** | 0.0% |
| tax-12 | attribution-claim | 1 | 0 | 0 | 100.0% | 0.0% |
| tax-13 | version-date-compound | 2 | 2 | 0 | 0.0% | 100.0% |

Totals:

```
human assertions                  26
material assertions               22
visible to the deterministic scan 11
material assertions it cannot see 12
audit-visible rate                50.0%
audit coverage where it had a value to check 0.0%
cases with nothing to scan        5 (tax-04, tax-08, tax-10, tax-11, tax-12)
cases reporting 100% while blind  tax-04, tax-11
```

The "compound" column (one claim spanning the whole answer) is 100% in every
case; see finding 3.

## Findings

### F1. The audit sees values, not propositions — half of material assertions are invisible

`assertions.candidates()` (`backend/app/verify/assertions.py:29-51`) recognises
money, percentages, measures, dates, versions and bare numbers. A sentence that
asserts a property, a location, a relation, a comparison or a superlative
produces **no candidate**, so it never enters the denominator.

`tax-02` is the original Eiffel paragraph in prose: nine material assertions, two
values found (1889 and 330 metres), and three of the four sentences contribute
nothing. Seven material assertions are invisible. The honest ratio is 22.2%; the
audit has nothing to compare against because the missing seven never became rows.

`tax-02` also exposes a hole inside the scanner's own vocabulary: "Roughly
7 million people visit it each year" produces no candidate at all. See F7.

This is the Eiffel gap in its general form. D-021 fixed the paragraph that
happened to contain numbers. A factual paragraph with no numbers is still
invisible.

### F2. "No values" is reported as 100% coverage

`CoverageReport.coverage` returns `1.0` when `total_assertions == 0`, and
`total_assertions` is `candidate_count + claims_without_candidate`. An answer
with no value and no stored claim therefore reports **100% coverage of nothing**.

Two cases make this a false statement about the run, not a harmless empty
denominator:

- `tax-04` — "Acme Pro is faster than Nimbus Pro." One material, checkable
  comparison. Audit: 100%, 0 rows. The evaluator cannot tell this apart from a
  run that verified everything.
- `tax-11` — "The Eiffel Tower is located in Paris. It was designed by Gustave
  Eiffel." Two material, checkable facts, no digits. Audit: 100%, 0 rows.

`tax-08`, `tax-10` and `tax-12` also report 100%, and there 100% is defensible:
opinion, ambiguous superlative and attribution are not material. The defect is
that the same number is printed for both situations, so a reader cannot tell
which one they are looking at.

### F3. One compound claim marks every value it spans as `checked`

`coverage._covering_claim` uses **containment** (`coverage.py:163-175`), so a
claim spanning a whole sentence covers every value in it. With one synthetic
claim spanning the whole answer, all 13 cases report 100% coverage — including
`tax-04` and `tax-11` where the claim would have to be an assertion the extractor
never made.

The design is deliberate (D-022: containment prevents one claim absorbing a
coordinate clause elsewhere in the sentence). The consequence is that `checked`
means *"a stored claim span contains this value"*, not *"this value was
verified on its own"*. `answer-03` in the gold set is the real shape: two prices
in one sentence, one compound claim, two `checked` values, one verification.

This is not a wrong verdict. It is a reporting limit: the audit cannot
distinguish "the claim covering this value was itself verified" from "the claim
containing this value was too coarse to verify the value".

### F4. The 100% extraction result does not generalise

As above: 18/18 expected assertions are in the scanner's own vocabulary. The
suite is a good regression test for the regexes. It is not evidence about
assertion recall, and it should not be quoted as if it were.

### F5. The gold set had a stale label

`answer-01`'s note said *"The Eiffel paragraph: 11 assertions across two
sentences"*, but the case contains **2** expected assertions. The 11-assertion
paragraph exists only in `DECISIONS.md` D-021. A reader comparing the note to
the data could not tell which document was stale. Fixed: the note now names the
two assertions the case actually holds and points at D-021 for the longer
example. `scripts/coverage_probe.py` reports `note_drift: []` for the file.

### F6. Two computed signals are never consumed

`assertions.signals()` computes `comparative` and `approximate`
(`assertions.py:122-130`), but `disposition_for()` only consults `subjective`,
`attribution` and `prediction` (`assertions.py:164-178`). A comparative with an
uncovered value is therefore `not_checked` (conservative, arguably correct) and
the two signals are measured but never used. Dead measurement, not a wrong
answer.

### F7. Two numeric shapes are missed, one of them mislabelled

The scanner's patterns (`backend/app/verify/assertions.py:29-51`) have no
scale words. Measured directly:

| text | scanner says |
|------|--------------|
| `7 million people` | no candidate |
| `2 million` | no candidate |
| `3 billion` | no candidate |
| `1.5 million` | `version: 1.5` |
| `10,100 tonnes` | `measure: 10,100 tonnes` |
| `7,000 people` | `number: 7,000` |

Two defects, both inside the vocabulary the scanner claims to cover:

1. **A rounded magnitude is invisible.** `7 million` is a bare number in
   meaning but not in shape, and the `number` pattern requires the digits to be
   adjacent (`\d{2,}` or a comma group). This is not a proposition the scanner
   cannot reach; it is a value the scanner was built for and still misses.
2. **`1.5 million` is classified as a `version`.** The version pattern
   (`\bv?\d+\.\d+...`) wins because both match at the same start and version
   outranks number. The disposition is still honest (`not_checked`), but the
   report labels a population as a software version.

`tax-02`'s "roughly 7 million" is the practical consequence: the sentence reads
as an assertion a reader would check, and the audit contributes nothing for it.

Not fixed in this pass: widening the patterns changes `total_assertions`, which
changes the coverage ratio, which can change the capped repair's targets and
therefore the model calls a run makes. That is a rule-area change (D-031) and
needs its own decision and a re-run of the evaluation, not a quiet regex edit.
The safer fix is a decision record plus a test asserting `7 million`,
`1.5 million` and `10,100 tonnes` are all `measure`/`number`, with the metric
change stated in the record.

## What this does not mean

- No verdict is wrong. Every claim that was verified was verified by the same
  settle chokepoint; nothing here touches `claims.status`.
- The audit is not lying about what it measures. It is explicitly a value
  scanner (`assertions.py:1-17`) and it reports exactly that.
- Missing coverage is not missing evidence. The 12 blind material assertions
  were never researched, because they never became claims — the failure is
  upstream of research.
- This does not justify adding a model call to "count assertions". That is WP-8
  territory and needs a spec, a budget and a taxonomy.

## Recommendations

| # | Task | Classification | State |
|---|------|----------------|-------|
| 1 | Report the blind spot instead of hiding it: count sentences with no candidate **and** no claim, and surface them beside `coverage`. Do not change `coverage` or `material_coverage`. | SAFE_TO_IMPLEMENT | **Implemented** — see below. |
| 2 | Add `eval/coverage_taxonomy.jsonl` to CI as a **measurement** (print the blind rate; do not fail the build). | SAFE_TO_IMPLEMENT | Open. `scripts/coverage_probe.py` exists and exits 0; not yet wired into a workflow. |
| 3 | Reach non-numeric propositions (property, location, relation, comparison) with a classifier or model pass. | SPEC_REQUIRED | Not started. Needs a taxonomy, a call budget, an evaluation set and a product-scope decision. This is WP-8. |
| 4 | Distinguish "checked individually" from "checked via a compound claim". | SPEC_REQUIRED | Not started. Interacts with extraction granularity; a naive fix would double-count or drop values. |
| 5 | Use `comparative` / `approximate` in `disposition_for`, or delete them. | SAFE_TO_IMPLEMENT | Open. Dead measurement, no wrong answer. |
| 6 | Repair the `answer-01` note. | SAFE_TO_IMPLEMENT | **Implemented** — the note names the two assertions the case holds. |
| 7 | Widen the scanner for rounded magnitudes (`7 million`, `3 billion`) and stop classifying `1.5 million` as a `version`. | SAFE_TO_IMPLEMENT, with a decision record | The fix is a regex edit; the consequence is a new denominator, new repair targets and possibly new model calls, so a decision record and an evaluation re-run are required. Recorded as D-031; pinned by F7. |

### Task 1, implemented

What changed:

- `backend/app/verify/coverage.py` — `CoverageReport.unscanned_sentences` (and
  the `unscanned` count) records every sentence with no scannable candidate
  **and** no claim that overlaps it. `coverage`, `material_coverage`,
  `dispositions` and `total_assertions` are computed exactly as before.
- `backend/app/verify/report.py` — the coverage section prints the count beside
  the ratio and adds a "Not visible to the scan" section listing the sentences.
- `backend/app/verify/store.py` — `put_coverage_audit` writes `unscanned` and
  `unscanned_sentences`; a missing value stores `0` / `[]`.
- `backend/migrations/032_ps05_coverage_unscanned.sql` — additive, idempotent
  columns with defaults, so an existing row reads as "not recorded".
- `backend/app/verify/orchestrator.py` — the terminal `run.completed` /
  `run.partial` event carries `coverage.unscanned`.

Evidence (command run; result observed):

```
.\.venv\Scripts\python.exe -m pytest tests\verify\test_coverage.py `
  tests\verify\test_pg_store.py tests\verify\test_coverage_parity_postgres.py -q
```

No failures; one skip. The skip is the live Postgres test: no `DATABASE_URL` is
exported here, so the round-trip is written and skipped rather than claimed.

Acceptance criteria, checked:

- A run over an answer with no scannable value and no claim reports
  `unscanned == 2` and `coverage == 1.0` unchanged —
  `test_a_sentence_with_no_value_and_no_claim_is_disclosed_not_rounded_away`.
- The rest of `test_coverage.py` passes unchanged, including
  `test_two_claims_from_seven_assertions_are_reported_as_a_gap`.
- The report says the number is a limit of the scanner —
  `test_the_report_says_what_the_ratio_cannot_see`.
- Local and Postgres agree — `test_coverage_audit_carries_the_unscanned_columns_as_a_mapping`
  (hermetic, executes the adapter) plus
  `test_the_disclosure_round_trips_and_matches_the_local_store` (marked
  `postgres`, skipped without a DSN).
- The evaluation gate is unchanged: `eval/run_eval.py` printed **Gate: PASS**,
  106 cases, 100.0% status accuracy, 136/136 quotes, 44/44 event dates.

What it does not do: it does not lower `coverage` for those sentences, does not
create claims, and does not change any verdict. A reader now sees the limit; the
metric still measures what it always measured.

### Task 2 (not done, on purpose)

Wiring the probe into CI is a workflow edit, and the probe's own totals changed
during this investigation (cases with nothing to scan are now counted
separately, because "100% of nothing" and "100% of something checked" are not
the same claim). Making it gate anything before that distinction settled would
have frozen a number that was still being corrected. The script exits 0 and is
safe to run by hand.

### Explicitly out of scope for this investigation

- No extraction prompt change.
- No second extraction call (the repair pass is unchanged and stays capped at one).
- No new verdict status, no change to `validator.settle`, no change to R1–R7.
- No change to the gold set's expected verdicts or to the evaluation gate.
