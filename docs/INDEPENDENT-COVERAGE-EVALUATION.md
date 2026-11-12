# Independent Assertion Coverage Evaluation (Post-5cc3340)

**Dataset version:** `independent-1.0.0`
**Data:** `eval/independent/v1/annotations.jsonl`
**Runner:** `eval/independent/run.py`
**Guidance:** `eval/independent/annotation-guidance.md`
**Baseline commit:** `5cc3340` (Coverage: disclose the sentences the deterministic
scan cannot read)
**Measurement cost:** $0.0000, 0 model calls, 0 Tavily calls, 0 database queries,
0 network calls, ~9-22 ms wall clock.

This milestone measures one number the repository did not have: **how many
material assertions a human reader finds does the current deterministic scanner
find**, including the shapes it was never built for. It does not change the
scanner, the rules, the schema, the prompts, the budgets, or any verdict.

## Why this exists

Three different measurements live in this repository. They answer different
questions and must never be merged into one accuracy figure:

| Measurement | Question | Where | Status |
|---|---|---|---|
| Verdict accuracy | Does the engine assign the expected status to the claims it is given? | `eval/run_eval.py` | unchanged, Gate PASS |
| Scanner-visible coverage | How much of the answer does the value scanner see? | `scripts/coverage_probe.py`, `CoverageReport.coverage` | unchanged |
| Independent assertion recall | How many human-identified material assertions does the scanner see? | this suite | new, measured |

The existing 100% extraction result is circular: every expected assertion in
`eval/assertion_gold.jsonl` is one of the value shapes the scanner was written
to find. It is a regression test for the regexes, not evidence about real-world
assertion recall. This suite is the non-circular counterpart, and its ground
truth was written from the sentence before the sentence was run through the
scanner.

**Outcome in one line:** material in-scope recall is **32/53 = 60.4%**, and
four cases report **100% coverage while the scanner sees nothing at all**. That
is the evidence WP-8 was waiting for, and it is why no WP-8 implementation was
authorized here.

---

## 1. Dataset schema and version

`eval/independent/VERSION` contains `independent-1.0.0`. The data is
`eval/independent/v1/annotations.jsonl`, one JSON object per input case.

| Dataset field | Type | Meaning |
|---|---|---|
| `id` | string | Stable input id (`ind-01` … `ind-29`) |
| `dataset_version` | string | Must equal `VERSION`; the validator rejects a mismatch |
| `category` | enum | One of the twelve case categories |
| `note` | string | One line on what the case tests |
| `answer` | string | The original input, byte-for-byte |
| `provenance` | object | `authored`, `basis`, `independence`, `scanner_output_used: false` |
| `assertions` | array | The independently annotated assertions |

| Assertion field | Type | Meaning |
|---|---|---|
| `id` | string | Stable assertion id (`ind-05-a02`) |
| `input_id` | string | The case id |
| `sentence_index` | int | Index into `app.verify.segment.segment(answer)` |
| `sentence` | string | The exact sentence at that index |
| `text` | string | The assertion span, present exactly once in `answer` |
| `span_start`, `span_end` | int | Absolute half-open offsets into `answer` |
| `value_text` | string \| null | The checkable value inside `text` |
| `value_span_start`, `value_span_end` | int \| null | Absolute offsets of `value_text` |
| `unit` | string \| null | Unit or period (`metres`, `EUR/month`, `people`) |
| `category` | enum | Semantic type: date, measurement, quantity, currency, percentage, count, duration, version, entity, relationship, ownership, location, comparison, ranking, property |
| `value_type` | enum | Date, measurement, quantity, currency, money, percentage, count, duration, version, none |
| `subject` | string | The entity the assertion is about |
| `property` | string | The attribute or relation asserted |
| `normalized` | string | One-sentence normalized reading |
| `material` | bool | Would a reader relying on the answer need this checked? |
| `in_scope` | bool | May the verification pipeline check it? |
| `assertion_type` | enum | `factual`, `subjective`, `predictive`, `ambiguous` |
| `rationale` | string | Why this span, category, materiality, and scope were chosen |

Size: **29 inputs, 57 assertions, 53 material and in scope, 39 value-bearing,
18 value-less.** All twelve required case categories appear:

| Case category | Cases | Purpose |
|---|---|---|
| `dates-historical` | 3 | event dates, opening years, treaty dates, fiscal periods |
| `measurements-quantities` | 5 | heights, weights, durations, populations, rounded magnitudes |
| `currency-units` | 3 | prices, valuations, recurring costs, currency names |
| `named-entities` | 3 | organizations and products used as subjects |
| `relationships-ownership` | 2 | founded-by, acquired, owns |
| `comparisons` | 1 | faster/longer than, with and without digits |
| `rankings-superlatives` | 2 | tallest, highest, most visited |
| `compound` | 5 | more than one assertion per sentence, including shared values |
| `non-numeric` | 1 | fully factual, fully checkable, no digits |
| `subjective` | 1 | taste presented as fact |
| `predictions` | 1 | forecast value and horizon |
| `ambiguous` | 1 | no referent, method, or comparison set |

`v1/` is frozen. A correction requires a new `v2/` directory and a `VERSION`
bump, so a published number cannot be edited into a better one.

## 2. Annotation methodology

The annotation unit is **one atomic proposition a careful reader would check**,
not one numeric token. Rules, in full in `eval/independent/annotation-guidance.md`:

- **Atomicity.** Split coordinated clauses. Do not split one predicate that
  asserts one value for several subjects.
- **Span minimality.** `text` is the shortest contiguous text containing the
  proposition; `value_text` is the value itself.
- **Value separation.** A proposition and the number it carries are separate
  assertions when each is independently checkable. Finding `100 percent` does
  not verify `Berkshire Hathaway owns 100 percent of GEICO`; both are annotated
  and the spans intentionally overlap (`ind-17`).
- **Materiality and scope are separate.** `material` asks whether a reader would
  need it checked; `in_scope` asks whether the pipeline may check it. A forecast
  can be material to a decision and out of scope as a present fact.
- **Subjective, predictive, ambiguous** statements are annotated so the
  denominator shows they were seen, then excluded from primary recall.
- **Many-to-one.** One value asserted for several subjects produces one
  assertion per subject sharing the identical value span (`ind-29`).
- **Independence.** The file carries no `kind`, `expected_disposition`,
  `expected_match`, or `scanner_kind` field. `provenance.scanner_output_used` is
  `false`, and the validator rejects a forbidden field.

Materiality was decided before measurement. In v1 every factual assertion is
material and in scope except the three out-of-scope cases; that is deliberate,
because the question is scanner recall, not materiality judgement. The single
materiality failure reported below was found by the runner, not declared in the
data.

## 3. Evaluation methodology

For each case the runner:

1. segments the answer with the **production** segmenter
   (`app.verify.segment.segment`);
2. takes the **production** value candidates
   (`app.verify.assertions.sentence_candidates`) per sentence;
3. matches every annotated assertion's `value_span` against candidates;
4. classifies every candidate as exact, partial, or false;
5. derives a failure class deterministically from observable facts;
6. runs `app.verify.coverage.audit` twice per case — with no claims and with one
   compound claim — to check what the production ratio would report;
7. aggregates precision, recall, F1, sentence-level detection, compound
   splitting, category recall, omission, duplicate/false-candidate rates, and
   the D-031 probe.

The runner is hermetic. It imports only `app.core.config`,
`app.verify.assertions`, `app.verify.coverage`, and `app.verify.segment`; an AST
test forbids network and model clients, and a socket test fails the build if the
suite opens a connection.

The suite is a **measurement, not a gate**. It exits 0 on a valid dataset and
prints the numbers. Pinned values live in
`backend/tests/test_independent_coverage_eval.py` so a quiet change to the
scanner or the data fails a test rather than silently moving the report.

## 4. Matching algorithm and limitations

Spans are half-open `[start, end)` intervals over the full answer.

| Relation | Definition | Recall credit |
|---|---|---|
| `exact` | Candidate span equals the annotation's `value_span` | yes |
| `partial` | One span properly contains the other, or they overlap without equality | no |
| `touched` | A candidate lies inside the assertion span, but the assertion has no value span or the candidate does not relate to its value | no |
| `miss` | No candidate intersects the assertion | no |

Decisions and their justification:

- **No partial credit.** `$19` inside `$19 billion` is a truncated value, not the
  asserted value. It is reported as partial and in the taxonomy, never counted
  as exact. Awarding partial recall would inflate the number by exactly the
  cases the scanner is worst at.
- **One-to-many.** Several candidates inside one annotated value yield at most
  one exact match: the one equal to the value span. They are never summed.
- **Many-to-one.** Several annotations sharing one value span are each credited
  by the single candidate and marked `shared_value`. This is the only
  many-to-one credit, and it is justified because one locatable value settles
  each subject's value assertion. `ind-29` records it and still records the
  entity assertion as missed.
- **Overlap is not equality.** An offset approximation is partial.
- **Precision is candidate-level.** Exact / all candidates. Partial and false
  both count against precision.
- **Numeric tokens inside names are false candidates.** `15` in `iPhone 15 Pro`
  matches no annotated value; it is false, not an entity recognition success.
- **Segmentation is a precondition.** A sentence disagreement fails dataset
  validation instead of being scored, so segmentation quality is inherited, not
  measured here.

Known matching limitations:

- A correct value attached to the wrong subject would be counted exact, because
  matching is span-based by design. The failure taxonomy cannot see subject
  confusion; `subject` is recorded but not scored.
- A proposition that overlaps a value but is annotated without a value span can
  only reach `touched`, never exact. That is intentional but conservative: it
  understates recall for prose propositions that happen to contain a number.
- One candidate can be exact for a shared value and partial for an overlapping
  proposition; the two facts are recorded separately and neither hides the other.

## 5. Precision, recall, F1

Measured on `independent-1.0.0`:

| Metric | Value |
|---|---|
| Material in-scope recall | **32/53 = 60.4%** |
| Partial recall (exact + partial) | 34/53 = 64.1% (diagnostic) |
| Touch rate (exact + partial + touched) | 36/53 = 67.9% (diagnostic) |
| Precision | **32/36 = 88.9%** |
| F1 | **71.9%** |
| Omission rate | 39.6% |
| False-candidate rate | 1/36 = 2.8% |
| Partial-candidate rate | 3/36 = 8.3% |
| Duplicate rate | 0.0% (1 shared-value candidate) |
| Value-type agreement among exact matches | 32/32 = 100.0% |

Sentence and compound level:

| Metric | Value |
|---|---|
| Sentences with material assertions | 27 |
| Fully covered | 9 (33.3%) |
| Touched by at least one candidate | 23 (85.2%) |
| Compound sentences (≥2 material in-scope assertions) | 20 |
| Compound sentences fully covered | 5 (25.0%) |
| Sentences with ≥2 material value assertions | 10 |
| Value-level splits correct | 7 (70.0%) |

The gap between the touch rate (67.9%) and recall (60.4%) is the scanner
reaching the neighbourhood of an assertion without stating the value the reader
asserted. The gap between sentence touch (85.2%) and sentence completeness
(33.3%) is the compound problem: a sentence is usually "seen", and usually not
finished.

## 6. Category performance

By case category (primary recall over material in-scope assertions):

| Case category | Exact / total | Recall |
|---|---|---|
| dates-historical | 5/7 | 71.4% |
| measurements-quantities | 7/9 | 77.8% |
| currency-units | 3/5 | 60.0% |
| named-entities | 3/7 | 42.9% |
| relationships-ownership | 2/5 | 40.0% |
| comparisons | 1/2 | 50.0% |
| rankings-superlatives | 0/3 | 0.0% |
| compound | 11/14 | 78.6% |
| non-numeric | 0/1 | 0.0% |
| subjective / predictions / ambiguous | 0/0 | n/a (all out of scope) |

By assertion category:

| Assertion category | Exact / total | Recall |
|---|---|---|
| date | 10/10 | 100.0% |
| measurement | 6/6 | 100.0% |
| percentage | 6/6 | 100.0% |
| count | 2/2 | 100.0% |
| version | 1/1 | 100.0% |
| currency | 6/8 | 75.0% |
| quantity | 1/3 | 33.3% |
| duration | 0/1 | 0.0% |
| entity | 0/4 | 0.0% |
| relationship | 0/3 | 0.0% |
| ownership | 0/1 | 0.0% |
| location | 0/2 | 0.0% |
| comparison | 0/2 | 0.0% |
| ranking | 0/3 | 0.0% |
| property | 0/1 | 0.0% |

The scanner is near-perfect on the value types it was built for (dates,
measures, percentages, counts, versions) and blind by construction to the
prose categories (entities, relationships, locations, comparisons, rankings).
That is not a regression; it is the shape of a value scanner, now quantified.

## 7. Failure examples

21 material in-scope assertions were not exact (2 partial, 2 touched, 17 miss).
The taxonomy is derived deterministically; the observable facts are kept in
`candidates_seen`.

| Class | Count | Example |
|---|---|---|
| `entity_recognition` | 4 | `Anthropic` (`ind-04`), `Acme and Nimbus` (`ind-23`), `iPhone 15 Pro` (`ind-27`), `Both the A2 and B2 models` (`ind-29`) |
| `relationship_extraction` | 5 | `Facebook acquired WhatsApp` (`ind-05`), `connects the Mediterranean Sea to the Red Sea` (`ind-09`), `headquartered in San Francisco` (`ind-14`), `Berkshire Hathaway owns 100 percent of GEICO` (`ind-17`, touched only), `Vienna` (`ind-26`) |
| `property_classification` | 6 | `world's most visited museum` (`ind-01`), `faster than the M3 chip` (`ind-06`), `the highest mountain above sea level` (`ind-07`), `ships with a two-year warranty` (`ind-13`), `two hours longer than the previous model` (`ind-15`), `the tallest building in the world` (`ind-24`) |
| `numeric_recognition` | 4 | `$19 billion` captured as `money: $19` (`ind-05`), `7 million` no candidate (`ind-19`), `3 billion euros` no candidate (`ind-21`), `eleven months` no candidate (`ind-24`) |
| `version_vs_quantity` | 1 | `1.5 million` captured as `version: 1.5` (`ind-20`) |
| `materiality_classification` | 1 | `Vienna` excused as a prediction (`ind-26`) |
| Expected exclusions | 4 | subjective ranking (`ind-10`), forecast value and horizon (`ind-11`), ambiguous ranking (`ind-12`) |

Three examples worth reading in full:

**Truncated value.** `ind-05`: "In 2014, Facebook acquired WhatsApp for
$19 billion." The scanner returns `money: $19`. The year and the acquisition are
missed; the price is partial. Recall 1/3, precision 50% for the case.

**A number that is part of a name.** `ind-27`: "The iPhone 15 Pro starts at $999
and ships on 2026-09-22." The scanner returns `number: 15` inside the product
name. The price and the date are exact. The entity assertion is `touched` and
classified `entity_recognition`; the `15` is a false candidate that lowers
precision to 66.7%.

**A false excuse.** `ind-26`: "The treaty was signed in Vienna on 15 May 1955."
`Vienna` is a material, in-scope location assertion. The sentence-level
disposition is `prediction`, because the prediction signal regex matches the
month name **May**. The assertion is therefore excused from verification by
wording, not by meaning. This is the only false excuse in the dataset and it is
a real defect class: a month name can switch a sentence's disposition.

### Cases reporting complete coverage while blind

Four cases report `CoverageReport.coverage == 1.0` with zero claims while at
least one material in-scope assertion is missed:

| Case | Answer | Coverage | Missed |
|---|---|---|---|
| `ind-07` | "Mount Everest is the highest mountain above sea level." | 100.0% | the superlative |
| `ind-09` | "The Suez Canal connects the Mediterranean Sea to the Red Sea." | 100.0% | the relation |
| `ind-21` | "The merger created a company valued at 3 billion euros." | 100.0% | the valuation |
| `ind-24` | "The Chrysler Building was the tallest building in the world for eleven months." | 100.0% | both assertions |

This is exactly the "100% of nothing" shape D-030 disclosed, now measured on
independent data. `5cc3340` added `unscanned_sentences` so the count is visible
beside the ratio; it did not change the ratio. The suite confirms the disclosure
works and the underlying blindness remains.

## 8. D-031 results

D-031 recorded four scanner shapes as known holes. This suite measures and
classifies them; it does not repair them.

| Text | Documented | Observed now | Classification |
|---|---|---|---|
| `7 million` | no candidate | no candidate | `scale_word_unrecognized` |
| `1.5 million` | `version: 1.5` | `version: 1.5` | `version_vs_quantity` |
| `3 billion` | no candidate | no candidate | `scale_word_unrecognized` |
| `10,100 tonnes` | `measure: 10,100 tonnes` | `measure: 10,100 tonnes` | `recognized_measure` |

All four still match the documented observation. In dataset terms: `7 million`
(`ind-19`) and `3 billion euros` (`ind-21`) are misses, `1.5 million` (`ind-20`)
is partial and classified `version_vs_quantity`, and `10,100 tonnes` (`ind-22`)
is exact. The other two D-031 numbers — `2 million` and `3 billion` without a
currency — are not dataset cases; the probe covers `3 billion` as the documented
example.

A regression test pins these four observations. Changing any of them is a
scanner decision that moves `total_assertions`, the coverage ratio, and the
capped repair's targets, and it requires its own decision plus a re-run of both
suites. That is why it was not done here.

## 9. Regression results

No production file changed. The guards in
`backend/tests/test_independent_coverage_eval.py` pin the contract:

| Guard | Pinned value |
|---|---|
| `RULE_VERSION` | `tev-1.2.0` |
| `eval/gold.jsonl` SHA-256 | `8e05bf12…c66435e` |
| `eval/gold_generated.jsonl` | `f86ff701…c5a66a20` |
| `eval/gold_live.jsonl` | `226abfee…652c40ba0` |
| `eval/assertion_gold.jsonl` | `51d0cafe…39383b83` |
| `eval/coverage_taxonomy.jsonl` | `c8a00cc7…5e8a2ce6` |
| `eval/run_eval.py` | `ac453e84…3e92c1b3` |
| `eval/extraction_suite.py` | `1d5a7acf…4d0c2ba56248` |
| `v1/annotations.jsonl` | `c7854823…a8c301d99c9bdca` |
| Eiffel two claims | total 2, checked 2, coverage 100%, material 100%, unscanned 0 |
| Uncovered measure | total 1, checked 0, coverage 0%, unscanned 0 |
| No value, no claim | total 0, coverage 100%, unscanned 1 |
| Unscanned sentence | total 1, checked 1, coverage 100%, unscanned 1 |

Verdict gate re-run unchanged (`eval/run_eval.py`):

- 106 cases, **107 claims**
- status accuracy **100.0%**
- false `supported` **0**, false `changed` **0**
- quote validity **100.0% (136 quotes)**
- event-date accuracy **100.0% (44 checks)**
- recorded model cost **$0.0000**
- known accepted failure: `scope-only-ivy` (status matches; reason `no_source`
  instead of `scope_mismatch`)
- **Gate: PASS**

Both suites were run separately, as required. The old suite reads no file from
`eval/independent/`; the new suite reads no gold file from the old suite except
to hash it.

## 10. Files

Created:

- `eval/independent/VERSION` — `independent-1.0.0`
- `eval/independent/v1/annotations.jsonl` — 29 cases, 57 assertions
- `eval/independent/annotation-guidance.md` — schema, rules, matching, metrics, taxonomy, limitations
- `eval/independent/run.py` — offline runner and report
- `backend/tests/test_independent_coverage_eval.py` - guards for the dataset, the runner, the pinned metrics, and the production invariants
- `docs/INDEPENDENT-COVERAGE-EVALUATION.md` — this report

Updated (documentation only):

- `ROADMAP-POST-WP10.md` — baseline `5cc3340`, phase 2b recorded, priority order
- `DECISIONS.md` — D-032
- `AGENTS.md` — the independent suite command and its boundary

Not touched: `eval/run_eval.py`, `eval/gold*.jsonl`,
`eval/assertion_gold.jsonl`, `eval/coverage_taxonomy.jsonl`,
`eval/extraction_suite.py`, any file under `backend/app/`, any migration, and
the four pre-existing untracked files (`test_demo_snapshot.py`,
`test_gold_generator.py`, `freeze_demo_snapshot.py`, `seed_demo.py`).

## 11. Reproduce

From the repository root (PowerShell):

```powershell
# the new measurement (offline, zero cost)
backend\.venv\Scripts\python.exe eval\independent\run.py

# machine-readable form
backend\.venv\Scripts\python.exe eval\independent\run.py --json
backend\.venv\Scripts\python.exe eval\independent\run.py --out result.json

# its guards
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_independent_coverage_eval.py -q

# the unchanged verdict gate
backend\.venv\Scripts\python.exe eval\run_eval.py

# the whole hermetic backend suite
backend\.venv\Scripts\python.exe -m pytest backend\tests -q

# the scanner-visible coverage probe
backend\.venv\Scripts\python.exe scripts\coverage_probe.py
```

Observed in this session: runner recall **60.4%**, precision **88.9%**, F1
**71.9%**; guards passed; verdict gate **Gate: PASS**.

## 12. Unresolved limitations

- **One annotator, no inter-annotator agreement.** A second reader could split
  or scope some propositions differently. The file records rationale per
  assertion so a disagreement is inspectable, but no agreement statistic exists.
- **Procedural independence, not proven independence.** The file has no
  scanner-shaped fields and records `scanner_output_used: false`; the authoring
  order cannot be proven from the artifact alone.
- **Small n.** 29 inputs and 57 assertions expose failure classes, not
  population prevalence. Category rates with one or two items are unstable; the
  counts are printed beside every rate for that reason.
- **Value scanner only.** The suite measures `assertions.candidates` and the
  coverage audit. It does not measure claim extraction, research planning,
  evidence mapping, settlement, temporal decisions, or claim duplication.
- **Conservative matching.** Prose propositions that overlap a value cannot
  reach exact, which understates recall for those shapes. The touch rate is
  reported so the gap is visible.
- **Subject confusion is not scored.** A correct value attached to the wrong
  subject would count exact.
- **Production segmentation is inherited.** A segmentation disagreement fails
  validation instead of being measured.
- **No live path.** No model, Tavily, database, or network call is made. A live
  failure outside the value scanner is out of scope for this suite.
- **The dataset is not a WP-8 taxonomy.** It characterises what is missed; it
  does not define the assertion taxonomy a future auditor would need, which
  remains spec work.

## What this does and does not justify

It justifies saying, with evidence: the deterministic scanner sees **60.4%** of
material in-scope assertions a reader finds, is **near-perfect on values it was
built for**, is **blind to entities, relations, comparisons and rankings**, and
**reports 100% coverage on four answers it cannot read at all**. It also
justifies two targeted, pre-implementation findings: the `May`/prediction
interaction that excuses `Vienna`, and the month-name/month-number ambiguity in
the disposition signals.

It does **not** justify changing the scanner, the coverage ratio, the
denominator, the settlement path, or the extraction prompt. Widening the
patterns moves `total_assertions`, the ratio, and the model budget; that is a
metric change with its own decision, its own tests per shape, and a re-run of
both suites. WP-8 remains deferred until a spec, a taxonomy, and a budget are
approved.
