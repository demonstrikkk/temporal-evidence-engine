# TEV Post-WP-10 Development Roadmap

**Repository:** `temporal-evidence-engine`  
**Baseline commit:** `5cc3340` – Coverage: disclose the sentences the deterministic scan cannot read  
**Branch:** `main`  
**Working tree:** clean (4 untracked files pre-date WP-10)

## 1. Baseline status

Verified against the current repository, not assumed from prior summaries.

### Commit
`5cc3340 Coverage: disclose the sentences the deterministic scan cannot read` –
HEAD of `main`, two commits past `60843c1` (`4f8b376` trace integrity,
`5cc3340` additive coverage disclosure).

### Gate results (observed in this session)

| Suite | Result |
|-------|--------|
| Backend (`backend\.venv\Scripts\python.exe -m pytest backend\tests -q`) | passed, no failures (16 skipped, all Postgres/live) |
| Evaluation (`backend\.venv\Scripts\python.exe eval\run_eval.py`) | Gate PASS — 106 cases, 107 claims, 100.0% status accuracy |
| Evaluation — false `supported` | 0 |
| Evaluation — false `changed` | 0 |
| Evaluation — quote validity | 136/136 |
| Evaluation — event-date accuracy | 44/44 |
| Independent assertion coverage (`backend\.venv\Scripts\python.exe eval\independent\run.py`) | recall 32/53 = 60.4%, precision 32/36 = 88.9%, F1 71.9%; four cases report 100% coverage while blind; $0.0000 |
| Independent suite guards (`backend\tests\test_independent_coverage_eval.py`) | passed |
| OTLP exporter tests (`test_otel_exporter.py`) | passed |

These numbers are the **regression baseline**. Any later change must reproduce them against the same gold set and its original expectations.

### Configuration and migration state

- `RULE_VERSION = "tev-1.2.0"` — a module constant in `backend/app/core/config.py`, deliberately **not** a `Settings` field and not readable from `.env` (D-015).
- Migrations `000`–`031` exist in `backend/migrations/`. TEV tables live in schema `tev` (migrations `014`–`031`).
- Two persistence adapters: `LocalStore` (JSONL dev store) and `PgStore` (Postgres). Both are exercised by tests.
- Bifrost is the single model interface; no provider SDK calls anywhere under `app/verify/`, `app/research/`, or `app/api/` (enforced by `test_no_direct_providers.py`).
- OTLP exporter lives in `backend/app/verify/otel.py`; trace recording in `backend/app/verify/trace.py`; propagation wired through Bifrost (`_post`) and Tavily (`_post`); FastAPI middleware in `backend/app/main.py`.

### Untracked pre-existing files (not part of WP-10, not to be conflated with it)
- `backend/tests/verify/test_demo_snapshot.py`
- `backend/tests/verify/test_gold_generator.py`
- `scripts/freeze_demo_snapshot.py`
- `scripts/seed_demo.py`

### Phase progress (recorded, not predicted)

| Phase | State | Evidence |
|-------|-------|----------|
| 0 — baseline | done | commit, config, migrations and both gates reproduced before any change |
| 1 — WP-10 validation | done | `docs/WP10-VALIDATION.md`: nine defects reproduced and fixed, contracts pinned by `test_otel_validation.py`, overhead measured by `scripts/otel_overhead.py` |
| 2 — assertion coverage | done | `docs/ASSERTION-COVERAGE.md`: 13-case taxonomy, 12 of 22 material assertions invisible to the scanner, the 100% extraction result shown to be circular |
| 2a — coverage disclosure | done | additive `unscanned_sentences` (migration `032`), no ratio or verdict change; evidence in `docs/ASSERTION-COVERAGE.md` |
| 2b — independent assertion coverage evaluation | done | `docs/INDEPENDENT-COVERAGE-EVALUATION.md`: 29 independently annotated inputs, 57 assertions, recall 60.4%, precision 88.9%, F1 71.9%; suite version `independent-1.0.0`; production unchanged |
| 3 — execution hardening | not started | — |
| 4 — research efficiency | not started | — |
| 5 — WP-8 design decision | not started | needs explicit approval |

Not claimed: a live OTLP collector round-trip (no collector in this
environment) and SSE trace propagation (the rail is not span-based; there is
nothing to propagate). Both are deployment questions, recorded in the WP-10
validation report.


## 2. Protected architecture and product invariants

These are treated as the verification contract. Future work must preserve them unless an explicit, separately approved decision changes them.

| Invariant | Source | Meaning for future work |
|-----------|--------|------------------------|
| Deterministic settlement is the only route to `verified` | D-003, BUILD-PLAN §8.1 | `app/verify/settlement.py` calls `validator.settle`; no other module may promote a cell to `verified`. `test_promotion_chokepoint.py` enforces this statically. |
| Model proposes; code decides | D-004, BUILD-PLAN §6 hard boundary | `relationship.py` proposes; `settlement.py` validates; `decision.py` assigns `claims.status`. No model output copied into `claims.status`. |
| Four timestamps, never merged | D-005 | `answer_generated_at`, `publication_date`, `event_date`, `retrieved_at` are separate. Unknown `event_date` is `missing`; it never falls back to `publication_date`. `test_no_publication_as_event.py` enforces this statically. |
| Seven closed verdicts only | D-021 | `claims.status` is exactly `pending | supported | changed | contradicted | unverifiable`. No sixth status (e.g. `not_checked`, `subjective`). |
| Scope gate filters before every rule | D-020, `decision.py` | Scope-incompatible contradictions leave the contradiction set before R1–R7 run. Never weakened to improve benchmark scores. |
| Bifrost is the only model path | D-007 | Backend speaks only `POST {BIFROST_BASE_URL}/v1/chat/completions`. No direct provider SDKs. |
| OTLP is additive, never a dependency | WP-10 spec | Disabled by default (`OTEL_ENABLED=false`); unavailable collector never changes a verdict or blocks a run; export failures logged and swallowed. |
| Privacy by default | WP-10 spec | No provider keys, auth headers, or raw user content exported by default (`OTEL_CAPTURE_CONTENT=false`). |
| Append-only source revisions | WP-10 spec + BUILD-PLAN §7 | Revisions keyed by `(source_id, content_hash)`; same hash reuses the row, never overwrite stored text. |
| Cache is part of the contract | D-006 | `model_calls` / `research_cache` make replays free and snapshot mode real. A replayed row is labelled `snapshot` with its original timestamp. |
| OTLP does not change verdicts | WP-10 spec | `verification_events`, `model_calls`, `source_revisions`, `claims`, `claim_evidence` remain the source of truth. OTLP is observability export only. |

## 3. Safe improvements — implementable now (SAFE_TO_IMPLEMENT)

Work that strengthens the existing system without changing product scope, verification semantics, or architecture.

### A. Regression and release hardening (highest priority)

**Classification:** SAFE_TO_IMPLEMENT

The existing passing suite is good, but a large passing suite does not guarantee correct behavior across execution paths. All changes below preserve current contracts.

Possible work:
- Establish a reproducible baseline report tied to commit, migration version, evaluation dataset version, and configuration.
- Verify the gold set runs consistently from a clean environment.
- Add regression tests for previously observed failures (citation marker as refutation, other-plan price refutation, `$10 per member per month` parse, scope-blocked contradiction, R7 reachability) — without changing expected behavior of existing cases.
- Test local persistence and Postgres persistence against the same contract.
- Test incomplete runs, failed extraction, failed model calls, partial research.
- Verify a failed stage cannot accidentally produce a successful final verdict.
- Verify cached and uncached execution preserve the same correctness semantics.
- Verify migrations apply from a fresh database and upgrade from the current schema.

Deliverable: a repeatable release gate that answers "is this change safe to merge?"

### B. WP-10 observability validation

**Classification:** SAFE_TO_IMPLEMENT (validation only; fixes must be isolated)

WP-10 is complete, but its instrumentation should be proven useful during a real investigation.

Possible work:
- Follow one run from root trace through extraction → research → evidence mapping → settlement → temporal decision.
- Correlate Bifrost requests with application stages.
- Cover Tavily search and extraction spans, including failed and cached operations.
- Verify trace context propagation across async tasks and SSE.
- Test collector unavailability, slow export, dropped spans, application shutdown.
- Confirm no sensitive data, API keys, auth headers, or full source content is exported.
- Measure instrumentation overhead under concurrent runs.
- Verify the trace can explain a partial run, not just a successful one.

Deliverable: WP-10 validation report, missing-span list, and narrowly scoped instrumentation fixes only.

### C. Documentation and reproducibility

**Classification:** SAFE_TO_IMPLEMENT

Improve engineering documentation without changing architecture:

- One canonical Windows development setup guide.
- One documented Bifrost startup and configuration procedure.
- One documented local OTLP collector workflow.
- One clear explanation of local persistence mode.
- One Postgres migration and compatibility guide.
- One evaluation guide explaining dev / held-out / ablation results.
- One configuration reference (which env vars affect which stages).
- One troubleshooting guide (provider failures, Tavily failures, cache behavior, incomplete runs).
- One architecture document showing the real current pipeline.

Deliverable: a new developer can reproduce the existing demo and evaluation without guessing.

### D. Security and configuration hygiene (review only initially)

**Classification:** SAFE_TO_IMPLEMENT (review); GOVERNANCE_APPROVAL_REQUIRED if changes touch auth/tenancy/encryption

Useful checks that don't require BYOK or multi-tenant auth:

- Ensure credentials are never logged.
- Ensure secrets are not included in trace attributes.
- Check environment-variable handling and configuration validation.
- Check diagnostic endpoints don't expose sensitive information.
- Review error messages for accidental secret disclosure.
- Check local dev credentials are not mistaken for production credentials.
- Review CORS, network binding, and debug-mode defaults.

Do not modify authentication, tenant isolation, credential storage, encryption boundaries, or access-control architecture without security design and explicit approval.

## 4. Investigation-first improvements (INVESTIGATION_REQUIRED)

These should be investigated before any implementation is proposed.

### C2. Assertion coverage investigation (recommended priority)

**Classification:** INVESTIGATION_REQUIRED

Directly addresses the Eiffel Tower example: correct verdicts for investigated claims while missing material assertions in the input. Before building WP-8 Auditor, establish exactly where assertions disappear.

Run a fixed test collection containing:
- Short factual paragraphs
- Long paragraphs with many facts
- Multiple dates in one sentence
- Comparisons and rankings
- Historical superlatives
- Measurements and units
- Claims with several entities
- Subjective opinions
- Predictions and future statements
- Ambiguous or impossible-to-verify claims

For every test input record:
1. Material assertions (human-reviewed reference)
2. Extracted atomic claims
3. Compound claims that remain unsplit
4. Omitted assertions
5. Duplicated claims
6. Claims extracted but not researched
7. Claims researched but lacking usable evidence
8. Claims reaching settlement and final dispositions
9. Model calls, Tavily calls, tokens, latency, cache hits, estimated cost

Identify whether the problem is extraction, atomic decomposition, coverage checks, research planning, search selection, evidence mapping, settlement rejection, or presentation. **Do not change the extraction prompt or add model calls yet.**

Deliverable: assertion-coverage failure analysis with reproducible examples.

**Status (2b, done):** `docs/ASSERTION-COVERAGE.md` answered "where do assertions
disappear" on 13 hand-built cases. `docs/INDEPENDENT-COVERAGE-EVALUATION.md`
answers "how large is the gap" on an independently annotated set that is not
derived from scanner output. It measured material in-scope recall at 32/53
(60.4%), precision at 32/36 (88.9%), F1 at 71.9%, and found four cases where the
production ratio reports 100% while the scanner sees nothing. It changed no
production file and made no model or Tavily call. WP-8 still needs a spec, a
taxonomy, and a budget; the measurement now exists to size that work.

### D2. Research efficiency analysis

**Classification:** INVESTIGATION_REQUIRED

Get more useful evidence per search and per model call without reducing verification quality:

- Measure unique searches per run and per claim.
- Detect duplicate or semantically equivalent search queries.
- Measure whether one extracted source supports multiple claims.
- Identify repeated extraction of the same source.
- Measure cache effectiveness separately (search, page extraction, model completion, claim-evidence reuse).
- Identify expensive research operations that produce no evidence.
- Measure percentage of extracted pages producing usable evidence.
- Track claims unresolved after exhausting research budget.
- Check whether query budgets are consumed by redundant work.

Only after measuring should changes to research planning or cache policy be proposed. No adaptive routing, no scheduler, no additional research agent.

Deliverable: research efficiency report.

### E2. API and execution reliability

**Classification:** INVESTIGATION_REQUIRED

Focus on the existing FastAPI execution path:

- Verify run creation and finalization behavior.
- Verify SSE events arrive in correct order.
- Check whether a disconnected client affects a running verification.
- Check whether failed operations are represented accurately in run status.
- Check whether event histories and persisted records agree.
- Verify cancellation leaves runs consistent (if supported).
- Test concurrent runs for state leakage.
- Test repeated requests and duplicate submissions.
- Check error responses are diagnostic without leaking credentials.

Deliverable: execution reliability report and isolated fixes for confirmed defects.

## 5. Specification-first improvements (SPEC_REQUIRED / GOVERNANCE_APPROVAL_REQUIRED)

| Candidate | What the specification must resolve | Current recommendation |
|-----------|-------------------------------------|------------------------|
| **WP-8 Assertion Coverage Auditor** | Assertion taxonomy, coverage contract, omissions, evaluation, model-call budget, product-scope permission, model-call budget | Draft proposal only |
| **WP-9 Model Lab** | Internal eval harness vs user-facing product, model comparison methodology, benchmark integrity, cost accounting | Defer |
| **WP-12 Adaptive routing** | Quality/cost evidence, routing objective, safe fallback rules, scheduler dependency, rollback strategy | Defer |
| **WP-5 Freshness/reverify** | Background scheduling, freshness policy, next-check disclosure, D-028 compatibility | Remain blocked |
| **WP-6 BYOK** | Authentication, tenant isolation, key encryption, provider-key ownership, rotation, revocation, audit policy | Remain blocked |
| **WP-7 Chat** | Formal product-scope change, interaction model, conversation persistence, evidence-context semantics | Remain anti-scoped |

### WP-8 deserves the first design review

If governing documents permit it, make WP-8 small and measurable:

- Input: paragraph with multiple factual assertions.
- Output: assertion inventory (number every material assertion), claim mapping (one-to-many), coverage report (omissions, compound claims, ambiguous statements, unsupported results).
- Only properly extracted claims proceed through existing research + deterministic verification.
- Final report: what was checked, what wasn't, why not, what remains unresolved.
- The auditor must **not** grant `verified`/`changed`/`contradicted`, must not replace deterministic settlement, must not turn missing evidence into contradiction.
- Subjective statements and predictions need an explicit disposition, not silent dropping.

WP-8 compatibility with `PRODUCT.md` and `BUILD-PLAN.md` must be checked before any implementation.

## 6. Blocked and anti-scoped work (leave untouched)

| WP | Disposition | Reason |
|----|-----------|--------|
| WP-5 | BLOCKED | Background scheduler dependency; `BUILD-PLAN §2` defers scheduler/cron daemon. Freshness is explicitly "not a verdict" (D-028). |
| WP-6 | BLOCKED | Security-sensitive credential management; multi-tenant requirements; autonomy policy pause on auth/tenant/encryption boundaries. |
| WP-7 | ANTI_SCOPED | `PRODUCT.md §7`: "chat interface" is explicitly "What this product will not do." |
| WP-9 | SPEC_REQUIRED then likely DEFER | No approved spec; no benchmark integrity or cost-accounting model. |
| WP-12 | SPEC_REQUIRED then likely DEFER | Requires quality/cost measurement + scheduler (both absent/anti-scoped). |

## 7. Recommended execution order

Each phase produces its own evidence, acceptance criteria, and commit. Do not run all phases as one task.

| Phase | Task | Classification | Output |
|-------|------|----------------|--------|
| 0 | Establish baseline — confirm commit, config, migrations, run all gates | Verification | Baseline and regression report |
| 1 | Validate WP-10 instrumentation (trace coverage, failure scenarios, privacy, overhead) | SAFE_TO_IMPLEMENT | WP-10 validation report + isolated fixes |
| 2 | Diagnose assertion coverage (Eiffel Tower root cause) | INVESTIGATION_REQUIRED | Assertion-coverage failure analysis |
| 3 | Harden existing execution (API, SSE, persistence, caching, error handling) + regression tests | SAFE_TO_IMPLEMENT | Reliability improvements with regression evidence |
| 4 | Research efficiency analysis | INVESTIGATION_REQUIRED | Research efficiency report |
| 5 | WP-8 design decision — check governing docs, draft spec, define acceptance criteria, obtain explicit decision | SPEC_REQUIRED → GOVERNANCE_APPROVAL_REQUIRED | Approved spec or documented decision not to proceed |
| 6 | Reassess roadmap — revisit WP-9, WP-12, WP-5, WP-6, WP-7 only after their own checks | — | Roadmap update |

Priority order (as set by the user):

1. **External OTLP collector validation** — blocked in this environment; no
   collector exists. Deployment check, not claimed.
2. **Postgres coverage parity** — the parity test exists and skips without
   `DATABASE_URL`; running it needs a database, not a code change.
3. **Independent assertion benchmark** — **done** in 2b
   (`docs/INDEPENDENT-COVERAGE-EVALUATION.md`, `independent-1.0.0`).
4. **Scanner blind-spot characterization** — **done** in 2b: 60.4% material
   recall, 0% on entities/relations/locations/comparisons/rankings, four
   "100% while blind" cases.
5. **Research efficiency analysis** — investigation only, no optimization yet.
6. **WP-8 spec** — deferred until 1-4 are settled and a spec is approved.

Phase ordering in the table above is historical. The live order is the six-item
list, and items 1 and 2 are environment-blocked rather than work-blocked. Items
3 and 4 are measured; do not re-open them without a dataset version bump.

## 8. Acceptance criteria for recommended next tasks

### Phase 0 — Baseline
- [ ] `git log` shows `5cc3340` at HEAD; working tree clean of WP-11+ changes.
- [ ] `pytest backend/tests -q` passes with no failures (Postgres/live skipped without `DATABASE_URL` and provider credits).
- [ ] `eval/run_eval.py` reproduces Gate PASS (106 cases, 100% accuracy, 0 false supported/changed, 136/136 quotes, 44/44 event dates).
- [ ] `RULE_VERSION == "tev-1.2.0"` in `app/core/config.py`; not a Settings field.
- [ ] Migration files `000`–`031` present; schema `tev` tables exist.

### Phase 1 — WP-10 validation
- [ ] One end-to-end run traceable from root trace through settlement and temporal decision.
- [ ] Bifrost requests correlate to correct application stages.
- [ ] Tavily search/extract spans visible, including failed and cached.
- [ ] Collector unavailability does not change verdict or block run.
- [ ] No API keys, auth headers, or full source content in exported attributes.
- [ ] Any instrumentation fix is isolated and keeps existing tests green.

### Phase 2 — Assertion coverage investigation
- [ ] Fixed input collection covers all listed categories.
- [ ] Human-reviewed reference assertions recorded per input.
- [ ] Extracted claims compared against reference (omitted, compound, duplicated).
- [ ] Failure stage identified (extraction / decomposition / research / settlement / presentation).
- [ ] Reproducible examples with model calls, Tavily calls, tokens, latency, cache, cost.
- [ ] No extraction prompt change, no added model calls.

### Phase 2b — Independent assertion coverage evaluation (done)
- [x] Dataset is versioned (`independent-1.0.0`), independently annotated, with stable ids, input id, sentence, exact span, normalized assertion, subject, property, value and unit, category, materiality, scope disposition, rationale.
- [x] All twelve required case categories present.
- [x] Runner reports precision, recall, F1, sentence-level detection, compound splitting, category recall, omission, duplicate, false-candidate, and "complete coverage while missed".
- [x] Matching rules defined and justified; no partial credit without justification.
- [x] Failure taxonomy derived deterministically from observable facts.
- [x] D-031 examples measured and classified, not fixed.
- [x] Old evaluation and gold files untouched; production ratios, claim records, verdicts and `RULE_VERSION` pinned by regression tests.
- [x] Zero model, Tavily, database and network calls; cost $0.0000.

### Phase 3 — Execution hardening
- [ ] Each confirmed defect has a reproducible test before the fix.
- [ ] All fixes preserve existing contracts and gate results.
- [ ] Local and Postgres parity verified.
- [ ] Migration fresh-apply and upgrade tested.

### Phase 5 — WP-8 design decision
- [ ] Governing-document compatibility checked (`PRODUCT.md`, `BUILD-PLAN.md`, DECISIONS.md).
- [ ] Specification drafted: assertion taxonomy, coverage contract, model-call budget, evaluation methodology, acceptance criteria.
- [ ] Explicit go/no-go decision recorded; no implementation without approval.

## 9. Files and architecture areas that must NOT be touched (without separate approval)

- `backend/app/verify/settlement.py` — only promotion chokepoint; `test_promotion_chokepoint.py` enforces.
- `backend/app/verify/decision.py` — temporal decision rules R1–R7; do not weaken to improve benchmark scores.
- `backend/app/verify/orchestrator.py` verdict assembly — never writes a status a model suggested.
- `backend/app/bifrost/client.py` — single model interface; no direct provider SDKs.
- `backend/app/core/config.py` — `RULE_VERSION` must remain a code constant, not a Settings field.
- `backend/tests/verify/test_no_direct_providers.py` — provider boundary enforcement.
- `backend/tests/verify/test_promotion_chokepoint.py` — settlement chokepoint enforcement.
- `backend/tests/verify/test_no_publication_as_event.py` — four-timestamp enforcement.
- `eval/gold.jsonl`, `eval/gold_generated.jsonl`, `eval/gold_live.jsonl` — expected results must not be weakened or redefined.
- OTLP failure-isolation paths (`otel.py` emit/flush swallow, `trace.py` end/finish guards) — collector unavailability must never block a run.

## 10. Decision process for the user

| Agent's finding | Your decision |
|----------------|---------------|
| Confirmed defect with reproducible test | Approve narrowly scoped fix |
| Missing regression coverage | Approve tests first |
| WP-10 instrumentation gap | Approve isolated observability fix |
| Assertion extraction incomplete | Review evidence; approve dedicated specification |
| Research calls wasteful | Approve measurement first, optimization second |
| Requires scheduler or multi-tenancy | Keep blocked |
| Conflicts with `PRODUCT.md` | Reject until product governance changes |
| New product capability without a spec | Require spec first |

**Strongest recommendation:** The assertion-coverage investigation is now
measured rather than argued: 60.4% material in-scope recall, 88.9% precision,
and four answers reporting 100% coverage while the scanner sees nothing
(`docs/INDEPENDENT-COVERAGE-EVALUATION.md`). The next decision is not "should we
look at coverage" but "which of the measured failures, if any, is worth a
scanner change or a WP-8 spec". Every option moves the denominator or the model
budget and needs its own approval. Until then, the highest-value open work is
the two environment-blocked checks: an external OTLP collector round-trip and
Postgres coverage parity.

**Do not begin implementation until this roadmap has been reviewed and an individual next task has been explicitly approved.**