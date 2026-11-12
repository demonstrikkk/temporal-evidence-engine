# Independent assertion-coverage evaluation — annotation guidance

**Dataset version:** `independent-1.0.0` (`eval/independent/VERSION`)
**Data:** `eval/independent/v1/annotations.jsonl`
**Runner:** `eval/independent/run.py`
**Report:** `docs/INDEPENDENT-COVERAGE-EVALUATION.md`

This suite exists because the original extraction evaluation is circular: every
expected assertion in `eval/assertion_gold.jsonl` is one of the value shapes the
scanner was written to find. A 100% result there is a regression test for the
regexes, not evidence about real-world assertion recall. The ground truth here
was written from the sentence, before the sentence was run through the scanner.

It is also **not** the verdict gate. Three measurements live in this repository
and must never be blended into one accuracy number:

| Measurement | Question | Where |
|---|---|---|
| Verdict accuracy | Does the engine assign the expected status to the claims it is given? | `eval/run_eval.py` |
| Scanner-visible coverage | How much of the answer does the deterministic value scanner see? | `scripts/coverage_probe.py`, `CoverageReport.coverage` |
| Independent assertion recall | How many material assertions a human reader finds does the scanner find, including shapes it was never built for? | this suite |

## 1. What gets annotated

Every case is one input answer. For each sentence, annotate **every atomic
proposition a careful reader would check**. Do not annotate only the numeric
ones; that is the failure mode this suite is designed to detect.

The case set must cover all twelve shapes:

| Case category | What it is for |
|---|---|
| `dates-historical` | Event dates, opening years, treaty dates, periods |
| `measurements-quantities` | Heights, weights, durations, populations, rounded magnitudes |
| `currency-units` | Prices, valuations, recurring costs, currency names |
| `named-entities` | Organizations, products, places used as subjects or objects |
| `relationships-ownership` | Founded-by, acquired, headquartered-in, owns, connects |
| `comparisons` | Faster than, longer than, more than, with or without a number |
| `rankings-superlatives` | Tallest, highest, most visited, best in a stated scope |
| `compound` | More than one assertion in one sentence, including shared values |
| `non-numeric` | Fully factual, fully checkable, no digits anywhere |
| `subjective` | Taste, beauty, opinion; no source can settle it |
| `predictions` | Forecasts, future dates, projected values |
| `ambiguous` | No referent, method, or comparison set; unverifiable as stated |

A case may contain assertions from several shapes; the case category names its
primary purpose, and the assertion's own `category` names its semantic type.

## 2. Assertion schema

One JSON object per line. Case fields:

| Field | Meaning |
|---|---|
| `id` | Stable input id (`ind-01`) |
| `dataset_version` | Must equal `eval/independent/VERSION` |
| `category` | One of the twelve case categories |
| `note` | One line on what the case tests |
| `answer` | The original input text, byte-for-byte |
| `provenance` | `authored`, `basis`, `independence`, `scanner_output_used: false` |
| `assertions` | The independently annotated assertions |

Assertion fields:

| Field | Meaning |
|---|---|
| `id` | Stable assertion id (`ind-01-a02`) |
| `input_id` | The case id |
| `sentence_index` | Index into `app.verify.segment.segment(answer)` |
| `sentence` | The exact sentence text at that index |
| `text` | The assertion span, resolved exactly once in `answer` |
| `span_start`, `span_end` | Absolute offsets into `answer` |
| `value_text` | The checkable value inside `text`, or `null` |
| `value_span_start`, `value_span_end` | Absolute offsets of `value_text`, or `null` |
| `unit` | Unit or period (`metres`, `EUR/month`, `people`), or `null` |
| `category` | Semantic assertion category (date, entity, relationship, …) |
| `value_type` | Semantic value type (`date`, `measurement`, `money`, … , `none`) |
| `subject` | The entity the assertion is about |
| `property` | The attribute or relation asserted |
| `normalized` | One-sentence normalized reading |
| `material` | Would a reader relying on the answer need this checked? |
| `in_scope` | May the verification pipeline check it? |
| `assertion_type` | `factual`, `subjective`, `predictive`, or `ambiguous` |
| `rationale` | Why this span, category, materiality, and scope were chosen |

There is deliberately **no** `kind`, `expected_disposition`, `expected_match`,
or `scanner_kind` field. Those would make the ground truth a copy of the thing
being measured. The validator rejects an unknown category, a span that does not
resolve, a sentence that disagrees with production segmentation, a missing
rationale, or a value span outside its assertion span.

## 3. Annotation rules

### Atomicity

One assertion is one independently checkable proposition. Split coordinated
clauses ("costs €49 … while … costs €89"). Do not split a single predicate that
asserts one value for several subjects ("Both the A2 and B2 models cost $99")
into two *values*; see many-to-one below.

### Span minimality

`text` is the shortest contiguous text that contains the proposition and its
value. For a value assertion, `value_text` is the value itself. For a
value-less proposition, the span is the predicate plus its object ("connects
the Mediterranean Sea to the Red Sea"). A span may contain a separately
annotated value; `ind-17` intentionally overlaps the ownership proposition and
its `100 percent`.

### Value separation

If a sentence asserts a fact and gives a number for it, annotate both the
proposition and the value when each is independently checkable. Do not let a
numeric token stand in for the proposition: "Berkshire Hathaway owns 100
percent of GEICO" is an ownership assertion *and* a percentage assertion, and
finding the percentage does not verify the ownership.

### Materiality and scope

`material` answers "would a reader relying on this answer need it checked?".
`in_scope` answers "may the verification pipeline check it?". They are
recorded separately because a prediction can be material to a decision while
remaining out of scope as a present fact.

- factual + checkable → `material: true`, `in_scope: true`
- subjective → `material: false`, `in_scope: false`, `assertion_type: subjective`
- predictive → `material: false`, `in_scope: false`, `assertion_type: predictive`
- ambiguous → `material: false`, `in_scope: false`, `assertion_type: ambiguous`

Primary recall is computed over `material && in_scope`. Values inside
out-of-scope assertions are still matched for precision: the scanner should
capture a forecast's magnitude correctly even though the engine must not verify
the forecast as fact.

### Compound statements

Annotate each atomic assertion as its own row. If a sentence has three values,
there must be three value assertions. A sentence is "compound" for reporting
when it carries at least two material in-scope assertions.

### Ambiguous statements

If the sentence does not identify the comparison set, the method, the referent,
or the time, it is ambiguous. Do not repair it by guessing; record
`assertion_type: ambiguous` and `in_scope: false`, and say what is missing in
the rationale.

### Subjective statements

Evaluation words ("beautiful", "best", "must-see"), taste, and preference are
subjective. They are assertions about the speaker, not facts about the world.
Annotate them so the denominator shows they were seen, but exclude them from
the primary recall denominator.

### Predictive statements

A forecast, projection, or future date is `predictive`, not factual. Annotate
the value and the horizon, mark both out of scope, and state in the rationale
that the engine must not verify them as present facts.

### Many-to-one and shared values

When one value is asserted for several subjects ("Both the A2 and B2 models cost
$99 per month"), create one assertion per subject, all sharing the identical
`value_text` and value span. Matching credits the single candidate as exact for
each, records `shared_value`, and does not treat that as a duplicate. The
justification is explicit: one locatable price settles both subjects' price
assertions.

### Independence

Write the annotation from the sentence and the rationale. Do not run the
scanner first, do not copy a candidate's `kind` into `category`, and do not
back-fill an assertion because a candidate happened to appear. Structural
guards (no scanner-shaped fields, provenance `scanner_output_used: false`) make
this checkable but cannot prove it; see the limitations in the report.

## 4. Matching algorithm

The runner segments the answer with the production segmenter and takes the
production candidate finder's output. For each annotated assertion:

| Relation | Definition | Credit |
|---|---|---|
| `exact` | Candidate span equals the annotation's `value_span` | Full match |
| `partial` | One span properly contains the other, or they overlap without equality | No recall credit; reported separately |
| `touched` | A candidate lies inside the assertion span, but the assertion has no value span or the candidate does not relate to its value | No recall credit; reported separately |
| `miss` | No candidate intersects the assertion | No credit |

Rules and justifications:

1. **No partial credit for recall.** `$19` inside `$19 billion` is not the value
   the reader asserted; it is a truncation. It is reported as `partial` and in
   the failure taxonomy, never counted as exact.
2. **One-to-many.** If one annotation's value span contains several candidates,
   only the candidate whose span equals the value span is exact. The others are
   partial or false; they are never summed into more than one match.
3. **Many-to-one.** If several annotations share the identical value span, one
   candidate is exact for each and `shared_value` is recorded. This is the only
   case where one candidate credits more than one assertion.
4. **No span drift.** An overlap that is neither equality nor containment is
   `partial`, not exact. Offsets are compared as half-open intervals
   `[start, end)`.
5. **Precision is candidate-level.** Every candidate is one of: exact (equals at
   least one value span), partial (contained in or containing one), or false.
   Precision = exact candidates / all candidates. Partial and false both lower
   precision, because a value the scanner cannot state completely is not a
   correctly extracted value.
6. **A numeric token inside a named entity is false.** `15` in `iPhone 15 Pro`
   is emitted by the number pattern and matches no annotated value; it is a
   false candidate, not an entity match.
7. **Segmentation is production's.** If an annotation's sentence does not match
   the production segmenter, the dataset fails validation. Segmentation quality
   is therefore a precondition, not a measured outcome.

## 5. Metrics

| Metric | Definition |
|---|---|
| Assertion recall | exact / (`material && in_scope`) |
| Partial recall | (exact + partial) / (`material && in_scope`) — diagnostic only |
| Touch rate | (exact + partial + touched) / (`material && in_scope`) — diagnostic only |
| Precision | exact candidates / all candidates |
| F1 | harmonic mean of precision and recall |
| Omission rate | 1 − assertion recall |
| False-candidate rate | false candidates / all candidates |
| Duplicate rate | repeated candidates / all candidates. The finder deduplicates overlapping spans, so repeated emission is structurally 0; `shared_value_candidates` reports the many-to-one case instead |
| Sentence-level detection | sentences with ≥1 material assertion that are fully covered / touched |
| Compound splitting | for sentences with ≥2 material value assertions: all values exact and no candidate merges two value spans |
| Category recall | the same recall grouped by case category and by assertion category |
| Value-type agreement | among exact matches, the share whose scanner kind is semantically compatible with `value_type` |

A "complete coverage" case is one where `CoverageReport.coverage` with no claims
is `1.0` while at least one material in-scope assertion is missed. That is the
"100% of nothing" shape D-030 disclosed; the suite reports it rather than
changing the ratio.

## 6. Failure taxonomy

Assigned deterministically per missed or partial assertion; the observable
facts that triggered the label are kept in `candidates_seen` and the sentence.

| Class | Meaning | Example |
|---|---|---|
| `numeric_recognition` | The numeric expression is not captured whole, or at all | `7 million`, `3 billion euros`, `eleven months`, `$19` from `$19 billion` |
| `unit_normalization` | Digits captured but the unit or period is lost or changed | reserved; no case in v1 |
| `entity_recognition` | A named entity is not represented as an assertion | `Anthropic`, `iPhone 15 Pro` |
| `property_classification` | A proposition or attribute is not a value the scanner can represent | `the highest mountain above sea level` |
| `relationship_extraction` | Subject–verb–object relation not represented | `Facebook acquired WhatsApp`, `Vienna` |
| `compound_splitting` | One candidate merges two or more annotated values | reserved; no case in v1 |
| `context_interpretation` | Value found but its role/type is incompatible with the annotation | reserved; v1's only exact kind mismatches are compatible |
| `version_vs_quantity` | Dotted magnitude read as a version | `1.5 million` → `version: 1.5` |
| `materiality_classification` | An in-scope assertion is excused by sentence wording | `Vienna` in a sentence containing "May" is excused as a prediction, because the signal regex matches the month |
| `unsupported_wording` | Subjective, predictive, or ambiguous wording is out of scope by annotation | `the most beautiful sight`, `one of the best towers` |
| `not_a_failure` | Out-of-scope assertion, expected to be unverified | predictions, opinions, ambiguous rankings |

`expected_exclusions` in the payload lists the out-of-scope assertions. They are
not counted as scanner failures; they are counted so the denominator is honest.

## 7. D-031 examples

D-031 records four measured scanner shapes. The suite **measures and
classifies** them; it does not repair them and does not change the scanner:

| Text | Documented observation | Classification |
|---|---|---|
| `7 million` | no candidate | `scale_word_unrecognized` |
| `1.5 million` | `version: 1.5` | `version_vs_quantity` |
| `3 billion` | no candidate | `scale_word_unrecognized` |
| `10,100 tonnes` | `measure: 10,100 tonnes` | `recognized_measure` |

Any change to these observations must be an explicit scanner decision, followed
by a dataset/version review and a re-run of both the old and new suites. The
regression test pins the current observations so a quiet regex edit cannot pass
unnoticed.

## 8. Adding a case or changing v1

1. Add the case with all required fields, or bump to `v2/` and update `VERSION`.
2. Run `backend\.venv\Scripts\python.exe eval\independent\run.py`.
3. The validator must resolve every span and sentence; a failure is a dataset
   bug, not a scanner result.
4. Do not edit a published dataset version to make a number look better. Add a
   case or a version and state the change.
5. Re-run `eval/run_eval.py` separately. The suites are never blended.

## 9. Known limitations

- **One annotator.** No inter-annotator agreement exists; a second reader could
  reasonably split or scope some propositions differently.
- **Procedural, not proven, independence.** The file has no scanner fields and
  records `scanner_output_used: false`, but the order of authoring cannot be
  proven from the artifact alone.
- **Small n.** 29 inputs and 57 assertions are enough to expose categories of
  failure, not to estimate population prevalence. Category rates with a handful
  of items are unstable; the counts are printed beside every rate.
- **Value scanner only.** This measures `assertions.candidates` and the coverage
  audit. It does not measure claim extraction, research, settlement, or claim
  duplication.
- **Conservative matching.** Shared-value credit is justified for the value it
  shares, not for the proposition around it; `ind-29` still records the entity
  assertion as missed.
- **Production segmentation is a precondition.** A segmentation disagreement
  fails validation instead of being scored, so segmentation quality is inherited.
- **No live data.** No model, Tavily, database, or network call is made. A live
  failure mode outside the value scanner is out of scope.
