# PS05 — Temporal Evidence Engine — Build Plan

**This document is the build contract.** If a task is not in here, it does not get
built. If a task in here is cut, it is cut in the Cut List, in order, with a note.
Everything else is drift.

| | |
|---|---|
| Submission deadline | **2026-11-15** (Sun). Today is 2026-11-09 (Mon). 7 calendar days, ~6 working days. |
| Submission status | PS05 is marked *proposed* in the brief and Track B is provisional. **Confirm with the organizers before the last 48 h.** Not a reason to slow down. |
| Repo | `temporal-evidence-engine`, new origin, DataGoblin as `upstream` |
| Product name | Working title: **Temporal Evidence Engine**. Naming is a 10-minute Day-1 decision, not load-bearing. |
| Stack | Python 3.11+ / FastAPI / Postgres (JSONL parity kept) / React 18 + Vite 5 + Tailwind 3 (already in the fork) |
| Gateway | **Bifrost** (real, running, `maximhq/bifrost`) for all model traffic |
| Research | **Tavily** Search + Extract, crawler as guarded fallback |
| Verdict authority | **Deterministic code.** No model output is ever stored as a claim status. |

---

## 0. The one-paragraph product

A user pastes an AI-generated answer and the time it was generated. The system
splits it into atomic claims, researches each claim live with Tavily, attaches
exact quotable passages from stored source revisions, extracts the *event date*
(distinct from publication date and retrieval time), and then a deterministic
engine decides one of four outcomes per claim: **supported**, **changed**,
**contradicted**, **unverifiable** — with a reason code and a full evidence
trail. Every model call goes through Bifrost and is visible. Every counted quote
resolves against stored text. Unknown dates stay unknown.

The product is a **claim-level temporal verification workspace**, not a chatbot,
not a search summarizer, not a dataset builder.

---

## 1. Non-negotiables (violating any of these means "not done")

1. **Evidence integrity.** A claim can only be `supported` or `changed` if its
   quote resolves against the exact stored source text **and** `validator.settle`
   granted it. `settle` remains the only promotion chokepoint.
2. **Temporal integrity.** `publication_date`, `event_date`, `retrieved_at`,
   `answer_generated_at` are four separate fields. Unknown `event_date` never
   falls back to `publication_date`. A newer publication date alone can never
   produce `changed`.
3. **Verdict integrity.** Statuses are produced by `decision_engine` from stored
   evidence rows. The model proposes; it never decides. No LLM output is copied
   into `claim_status`.
4. **Gateway integrity.** Every model request goes through a running Bifrost
   gateway. No application-level direct provider clients. Fallback, budget,
   cost, latency and provider used are recorded and visible.
5. **Research integrity.** Tavily Search discovers, Tavily Extract retrieves,
   and search snippets are never used as evidence. Partial extraction failures
   are recorded as failures.
6. **Evaluation integrity.** False-supported and false-changed rates are
   measured on a held-out annotated set. No "92% true" anywhere unless
   calibrated.
7. **Replay integrity.** A cached/stored result is labelled as a snapshot with
   its original retrieval timestamp. It is never presented as live.

---

## 2. What is deliberately out of scope

| Deferred | Why |
|---|---|
| Custom Bifrost Go plugin / MCP server | Bifrost already routes, retries, falls back, budgets, logs. A plugin adds risk and zero score. |
| Vector DB / embeddings retrieval | Quote resolution and settlement matter; semantic recall is not the bottleneck at 120 claims. |
| Background scheduler / cron daemon | `next_check_at` + `Reverify now` demonstrates the concept honestly. Do not display a fake "monitoring" badge. |
| Multi-tenant auth, roles, billing | Not in the PS. Single operator. |
| Browser extension | Not the core journey. |
| Agent framework (LangGraph etc.) | A deterministic orchestrator is inspectable and testable. |
| Auto-rewrite before verification | Launders unsupported claims into prose. Rewrite is downstream only, and is the first cut. |
| Dataset-first UI, discovery wizard, planner caps | DataGoblin product surface. Kept in the repo as substrate, **unlinked** from the PS05 app shell. |

---

## 3. Cut List (apply top-down when behind; never cut the bottom)

1. Answer repair / rewrite (Feature 6) — drop from UI, keep the endpoint if free.
2. Temporal replay slider (Feature 2) — drop; the timeline in the inspector stays.
3. Watchlist page — drop the page; keep `next_check_at` + `Reverify now` in the inspector.
4. Gold set 120 → 80 claims — keep the metrics, report the smaller N honestly.
5. Ablation 3 pipelines → 2 (LLM-only baseline vs full PS05).
6. Search `news` topic / per-domain authority tuning — default `general` only.

**Never cut:** settlement gate, event-date separation, abstention reason codes,
evidence export, Bifrost visibility panel, failure paths, held-out eval.

---

## 4. Repository bootstrap (Day 1, first 90 minutes)

DataGoblin's working tree is **uncommitted by design**. A plain `git clone` would
lose the pacing/transport fixes. Clone for history, then overlay the working tree.

```powershell
$src = 'C:\Users\asus\Downloads\datagoblin'
$dst = 'C:\Users\asus\Downloads\temporal-evidence-engine'

git clone $src $dst                      # history + upstream objects
robocopy $src $dst /E `
  /XD .git .venv node_modules __pycache__ .pytest_cache .datagoblin-local `
      outputs editions video research inputs .agents .claude .opencode `
  /XF .env .env.bak-model-161920 tmp_*.log tmp_*.err tmp_*.py
# robocopy exit codes 0..7 are success

cd $dst
git remote rename origin upstream
git remote add origin <NEW_EMPTY_REPO_URL>
git add -A
git commit -m "Seed: DataGoblin substrate @8548fc5 + pacing/transport fixes"
git push -u origin main
```

Then, in the new repo:

- Keep `LICENSE` (MIT) unchanged.
- Add `NOTICE`:
  > Contains code derived from DataGoblin (MIT, Copyright (c) 2026 DataGoblin
  > contributors). Temporal claim verification, Bifrost integration, Tavily
  > research orchestration and the PS05 frontend are original work.
- Add `UPSTREAM.md`: upstream URL, seeded commit `8548fc5`, the exact list of
  reused modules (§9), and the rule "pull from upstream only by cherry-pick,
  never by merge".
- Add `PRODUCT.md` (one page, §0 of this file) — the anti-drift contract.
- Add `DECISIONS.md` — numbered decisions, date, why. Every engine rule change
  bumps `RULE_VERSION`.
- Rename the frontend package to `temporal-evidence-engine`.

### Repo hygiene rules

- Never merge `upstream/main` wholesale; the product surface diverged on purpose.
- `frontend/` is rewritten; old dataset routes are **unmounted, not deleted**,
  until the new journey works end to end.
- Backend keeps `app/services/*` untouched where reused. New code lives in
  `app/verify/`, `app/bifrost/`, `app/research/`, `app/api/verify.py`.
- Do not "clean up" DataGoblin modules to make them claim-shaped. Wrap.

---

## 5. Environment and configuration

`.env.example` (PS05 additions):

```ini
# --- Bifrost gateway: the ONLY model path -------------------------------
BIFROST_BASE_URL=http://127.0.0.1:8080
BIFROST_VIRTUAL_KEY=ps05-dev
BIFROST_MODEL_EXTRACTOR=groq/openai/gpt-oss-20b
BIFROST_MODEL_JUDGE=groq/openai/gpt-oss-20b
BIFROST_MODEL_REWRITER=groq/openai/gpt-oss-20b
BIFROST_TIMEOUT_S=60
BIFROST_MAX_RETRIES=2
BIFROST_RETRY_AFTER_CAP_S=20     # honour Retry-After, but never hold a worker longer
BIFROST_BUDGET_USD=5.00

# --- Tavily: the ONLY research path -------------------------------------
TAVILY_API_KEY=
TAVILY_SEARCH_DEPTH=advanced
TAVILY_MAX_RESULTS=5
TAVILY_MAX_URLS_PER_CLAIM=4
TAVILY_ALLOW_CRAWLER_FALLBACK=true

# --- Verifier ------------------------------------------------------------
# RULE_VERSION is a code constant (app/core/config.py), not a setting: a
# label that an env file can change without changing the rules is noise.
CLAIM_MAX_PER_ANSWER=40
RESEARCH_MAX_QUERIES_PER_CLAIM=3
EVENT_DATE_MODEL=bifrost-judge
CACHE_MODE=read-write          # read-write | read-only (snapshot/demo) | off
```

Rules:

- `CACHE_MODE=read-only` makes the demo reproducible: every model/Tavily call is
  served from `model_calls` / `research_cache`, and the UI must badge it
  `snapshot`.
- Secrets never enter `model_calls`, logs, exports or the frontend.
- Provider keys live **only** in Bifrost config. `GROQ_API_KEY` etc. are removed
  from the PS05 backend's runtime path (keep the DataGoblin modules importable,
  but the verifier never constructs them).

---

## 6. Architecture and module map

```
                       React workspace
                            |  REST + SSE
                            v
                     FastAPI (app/main.py)
                            |
                 app/verify/orchestrator.py
        +-------------------+--------------------+
        |                   |                    |
  claim_extractor     research/*            events/store
        |                   |                    |
        |              Tavily Search               Postgres
        |              Tavily Extract               (durable events)
        |              guarded crawler
        |                   |
        +--------- evidence/* (locate + settle)
                            |
                    decision_engine.py
                            |
                    claim_status + history
                            |
                    report_generator.py

  every model call  -> app/bifrost/client.py -> BIFROST -> provider(s)
```

### New backend modules (exact files)

| File | Responsibility | Reuse |
|---|---|---|
| `app/verify/schemas.py` | Pydantic models for answer, claim, evidence, verdict, report | — |
| `app/verify/intake.py` | Normalize answer, validate `generated_at`, unknown handling | — |
| `app/verify/claim_extractor.py` | LLM → atomic claims; offsets into the answer; type + volatility | `bifrost/client` |
| `app/verify/claim_normalizer.py` | subject/predicate/value/qualifiers normalization | `schemas/evidence.normalize_for_match` |
| `app/verify/value_normalizer.py` | Typed comparison (money, number, %, duration, version, date, quantity) | new |
| `app/verify/research_planner.py` | 2–3 targeted queries per claim, prior-value query included | `bifrost/client` |
| `app/research/tavily.py` | Search + Extract, partial-failure handling, caching | new |
| `app/research/sources.py` | canonical URL, dedupe, append-only revision storage | `deduper`, `guard_url`, `crawler` |
| `app/research/crawler_fallback.py` | guarded fetch when Extract fails | `providers/crawl/fetcher`, `politeness`, robots |
| `app/verify/evidence.py` | chunk windows + offset-preserving quote location | `schemas/evidence.locate_quote` |
| `app/verify/relationship.py` | Model proposes supports/contradicts/mentions_only/silent | `bifrost/client` |
| `app/verify/settlement.py` | Synthetic cell → `validator.settle`; persist offsets | `services/validator.settle` |
| `app/verify/temporal.py` | Event-date extraction with its own quote; temporal ordering | `services/temporal` helpers |
| `app/verify/decision.py` | Precedence table, reason codes, `changed` preconditions | new |
| `app/verify/freshness.py` | Half-life index + `next_check_at` | `services/confidence`? no — new |
| `app/verify/report.py` | Markdown + JSON evidence pack | new |
| `app/verify/orchestrator.py` | Stage state machine, events, cancellation, budgets | `services/runner` patterns |
| `app/bifrost/client.py` | The only model interface; records request id, tokens, cost | new |
| `app/api/verify.py` | HTTP routes + SSE | `events` patterns |

**Hard boundary:** `relationship.py` proposes, `settlement.py` validates,
`decision.py` decides. Three files. Do not collapse them into one LLM call.

---

## 7. Data model (migrations 014+)

Follow DataGoblin's migration contract: `backend/migrations/NNN_*.sql`, applied
in filename order, idempotent, additive, every new column with a default.

> **Important reuse decision.** Do **not** create a parallel source table that
> `settle` cannot read. Store source text in `source_revisions` and pass
> `source_revisions.content` straight to `settle(source_text=...)`. `pages` stays
> for the legacy runner; the verifier never depends on `run_id`.

```sql
-- 014_ps05_answers.sql
CREATE TABLE IF NOT EXISTS answers (
  id uuid PRIMARY KEY,
  prompt text NOT NULL DEFAULT '',
  answer_text text NOT NULL,
  generated_at timestamptz NULL,
  generated_at_source text NOT NULL DEFAULT 'unknown',  -- user_supplied|unknown
  submitted_at timestamptz NOT NULL DEFAULT now(),
  locale text NOT NULL DEFAULT 'en',
  source_urls jsonb NOT NULL DEFAULT '[]'::jsonb,
  domain_filter jsonb NOT NULL DEFAULT '[]'::jsonb,
  skip_non_factual boolean NOT NULL DEFAULT true
);

-- 015_ps05_runs.sql
CREATE TABLE IF NOT EXISTS verification_runs (
  id uuid PRIMARY KEY,
  answer_id uuid NOT NULL REFERENCES answers(id),
  status text NOT NULL DEFAULT 'queued',
  stage text NOT NULL DEFAULT 'queued',
  rule_version text NOT NULL DEFAULT '',  -- 021 drops this default; the writer supplies the version
  cache_mode text NOT NULL DEFAULT 'read-write',
  started_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz NULL,
  error jsonb NULL
);
CREATE INDEX IF NOT EXISTS verification_runs_answer_idx ON verification_runs (answer_id, started_at DESC);

-- 016_ps05_claims.sql
CREATE TABLE IF NOT EXISTS claims (
  id uuid PRIMARY KEY,
  answer_id uuid NOT NULL REFERENCES answers(id),
  run_id uuid NOT NULL REFERENCES verification_runs(id),
  ordinal int NOT NULL,
  claim_text text NOT NULL,
  span_start int NOT NULL,
  span_end int NOT NULL,
  subject text NOT NULL DEFAULT '',
  predicate text NOT NULL DEFAULT '',
  object_raw text NOT NULL DEFAULT '',
  object_normalized jsonb NOT NULL DEFAULT '{}'::jsonb,
  qualifiers jsonb NOT NULL DEFAULT '{}'::jsonb,
  claim_type text NOT NULL DEFAULT 'factual',       -- factual|time_sensitive|stable|opinion|prediction|ambiguous
  time_sensitive boolean NOT NULL DEFAULT false,
  volatility_class text NOT NULL DEFAULT 'moderate',-- ephemeral|high|moderate|low|stable
  temporal_scope jsonb NOT NULL DEFAULT '{}'::jsonb,
  extraction_confidence numeric NOT NULL DEFAULT 0,
  status text NOT NULL DEFAULT 'pending',           -- pending|supported|changed|contradicted|unverifiable
  reason_code text NULL,
  next_check_at timestamptz NULL,
  last_verified_at timestamptz NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (answer_id, ordinal)
);
CREATE INDEX IF NOT EXISTS claims_run_idx ON claims (run_id);
CREATE INDEX IF NOT EXISTS claims_due_idx ON claims (next_check_at);

-- 017_ps05_sources.sql
CREATE TABLE IF NOT EXISTS sources (
  id uuid PRIMARY KEY,
  canonical_url text NOT NULL,
  domain text NOT NULL,
  title text NOT NULL DEFAULT '',
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (canonical_url)
);
CREATE TABLE IF NOT EXISTS source_revisions (
  id uuid PRIMARY KEY,
  source_id uuid NOT NULL REFERENCES sources(id),
  content_hash text NOT NULL,
  content text NOT NULL,                 -- EXACT text that offsets index into
  published_date date NULL,
  published_date_source text NOT NULL DEFAULT 'unknown', -- tavily|meta|unknown
  retrieved_at timestamptz NOT NULL DEFAULT now(),
  extraction_method text NOT NULL DEFAULT 'tavily_extract', -- tavily_extract|crawler|manual
  tavily_request_id text NULL,
  fetch_outcome jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (source_id, content_hash)
);

-- 018_ps05_evidence.sql
CREATE TABLE IF NOT EXISTS evidence_spans (
  id uuid PRIMARY KEY,
  claim_id uuid NOT NULL REFERENCES claims(id),
  source_revision_id uuid NOT NULL REFERENCES source_revisions(id),
  quote_text text NOT NULL,
  start_char int NOT NULL,
  end_char int NOT NULL,
  event_date date NULL,
  event_date_quote text NULL,
  event_date_status text NOT NULL DEFAULT 'missing', -- explicit|relative|missing
  retrieved_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS evidence_spans_claim_idx ON evidence_spans (claim_id);

CREATE TABLE IF NOT EXISTS claim_evidence (
  id uuid PRIMARY KEY,
  claim_id uuid NOT NULL REFERENCES claims(id),
  evidence_id uuid NOT NULL REFERENCES evidence_spans(id),
  relationship_proposed text NOT NULL DEFAULT 'mentions_only',
  relationship_settled text NULL,        -- supports|contradicts|mentions_only|silent
  settlement_status text NULL,           -- verified|unverified
  settlement_reason text NULL,
  settlement_start int NULL,
  settlement_end int NULL,
  candidate_value_raw text NULL,
  candidate_value_normalized jsonb NULL,
  model_call_id uuid NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (claim_id, evidence_id)
);

-- 019_ps05_assertions_history.sql
CREATE TABLE IF NOT EXISTS claim_assertions (
  id uuid PRIMARY KEY,
  claim_id uuid NOT NULL REFERENCES claims(id),
  value_raw text NOT NULL,
  value_normalized jsonb NOT NULL DEFAULT '{}'::jsonb,
  valid_from timestamptz NULL,
  valid_until timestamptz NULL,
  evidence_id uuid NULL REFERENCES evidence_spans(id),
  is_original boolean NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS claim_status_history (
  id uuid PRIMARY KEY,
  claim_id uuid NOT NULL REFERENCES claims(id),
  run_id uuid NOT NULL REFERENCES verification_runs(id),
  previous_status text NULL,
  new_status text NOT NULL,
  reason_code text NULL,
  rule_version text NOT NULL,
  decided_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS verification_events (
  run_id uuid NOT NULL REFERENCES verification_runs(id),
  sequence int NOT NULL,
  event_type text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, sequence)
);

-- 020_ps05_research_cache.sql
CREATE TABLE IF NOT EXISTS research_queries (
  id uuid PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES verification_runs(id),
  claim_id uuid NOT NULL REFERENCES claims(id),
  query text NOT NULL,
  topic text NOT NULL DEFAULT 'general',
  depth text NOT NULL DEFAULT 'advanced',
  result_count int NOT NULL DEFAULT 0,
  tavily_request_id text NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research_cache (
  cache_key text PRIMARY KEY,            -- hash(tool, query/url, options)
  tool text NOT NULL,                    -- tavily_search|tavily_extract
  payload jsonb NOT NULL,
  fetched_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS model_calls (
  id uuid PRIMARY KEY,
  run_id uuid NULL REFERENCES verification_runs(id),
  claim_id uuid NULL REFERENCES claims(id),
  stage text NOT NULL,
  provider text NOT NULL DEFAULT '',
  model text NOT NULL DEFAULT '',
  bifrost_request_id text NULL,
  prompt_hash text NOT NULL,
  response_json jsonb NULL,
  input_tokens int NOT NULL DEFAULT 0,
  output_tokens int NOT NULL DEFAULT 0,
  cost_usd numeric NOT NULL DEFAULT 0,
  latency_ms int NOT NULL DEFAULT 0,
  fallback_attempts int NOT NULL DEFAULT 0,
  status text NOT NULL DEFAULT 'ok',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (prompt_hash, model, stage)
);
```

Notes:

- `evidence_spans.start_char/end_char` index into `source_revisions.content`.
  Never into a reduced/chunked string. Chunking keeps
  `(chunk_start, chunk_end)` and offsets are rebased before insert.
- `claim_evidence.settlement_start/end` are the offsets `settle` returned —
  kept separately so a re-settlement is auditable.
- `model_calls.UNIQUE (prompt_hash, model, stage)` is the **cache**. `CACHE_MODE`
  decides whether a miss is allowed to call the network.
- Status vocabulary is closed: `supported | changed | contradicted | unverifiable`
  plus `pending` before a decision. Add a CHECK constraint.

---

## 8. Verifier rules (frozen as `RULE_VERSION=tev-1.1.0`; see D-013)

### 8.1 Decision precedence

Inputs: only evidence rows where `relationship_settled != 'silent'` and, for
`supports`/`contradicts`, `settlement_status = 'verified'`. A settled row also
has to be a statement (three words minimum) and, for a contradiction, has to name
the claim's own qualifier; both are enforced in `app/verify/settlement.py`
(D-013).

| # | Condition | Status | Reason |
|---|---|---|---|
| 1 | No evidence with a locatable quote | `unverifiable` | `no_source` |
| 2 | Evidence exists; nothing settles as support; no valid contradiction | `unverifiable` | `no_entailment` |
| 2b | The only contradiction states a different currency, period or per-unit basis (and no support exists) | `unverifiable` | `scope_mismatch` |
| 3 | Valid contradiction refutes the claim; no support | `contradicted` | `contradicted_by_source` |
| 4 | Support exists and any contradiction is older than the supporting evidence | `supported` | `supported_by_source` |
| 5 | Support exists **and** a newer source settles a replacement value with `event_date > answer_generated_at` | `changed` | `value_changed_after_generation` |
| 6 | Support and contradiction coexist, either relevant `event_date` unknown | `contradicted` | `temporal_ambiguity` |
| 7 | Two credible, directly relevant sources conflict at the same effective time | `unverifiable` | `conflicting_evidence` |

**`changed` requires all eight** (and each is a unit test):

1. The claim is supported for the answer-time context.
2. Replacement refers to the same subject, predicate **and** scope (plan,
   geography, version, period, per-unit basis).
3. Replacement quote is locatable in stored text.
4. Replacement value passes normalization + `settle`.
5. `event_date` is known and quoted.
6. `event_date > answer_generated_at`.
7. Evidence establishes the replacement became effective, not merely that a
   newer article exists.
8. No later valid evidence reverses it.

Then, and only then:
`old_assertion.valid_until = replacement.event_date`,
`new_assertion.valid_from = replacement.event_date`.

If `answer_generated_at` is unknown: support/contradiction still allowed,
`changed` is impossible, and the UI must say why.

**Known tension, decide once and log in `DECISIONS.md`:** rule 6 returns
`contradicted` when event dates are unknown. `unverifiable/missing_event_date`
is arguably more honest and would lower false-`contradicted` errors. Default:
follow the table (it is explicit), revisit only if the held-out set shows rule 6
is the main error source.

### 8.2 Settlement mapping (the gate)

```python
def settle_cell(claim_value: dict, quote: str, source_text: str) -> dict:
    """One synthetic one-field record -> validator.settle. No new promotion path."""
    cell = {"value": claim_value, "verification_status": "unverified", "source": {}}
    return validator.settle(cell, status="verified", quote=quote,
                            source_text=source_text)
```

- `verified` returned → relationship may count.
- `unverified` returned → relationship is rejected; store
  `verification_status` + `source.start/end` + `not_verified_because`.
- A failed support is **not** a contradiction. Contradiction requires its own
  synthetic cell carrying the **replacement value** (or an explicit negation)
  and its own locatable quote.
- `test_promotion_chokepoint.py` must keep passing unchanged. PS05 code must not
  assign the literal `"verified"` to `verification_status` anywhere.

### 8.3 Value normalization

`value_normalizer.compare(a, b) -> {equivalent, reason}` with typed shapes:

```json
{"type":"money","amount":"99","currency":"USD","period":"month","per_unit":null,"precision":2}
{"type":"number","amount":"1200","unit":"employees","period":null,"precision":0}
{"type":"percent","amount":"10","basis":"percentage_points","period":null}
{"type":"quantity","amount":"10","unit":"GB","unit_convention":"decimal"}
{"type":"version","parts":["2","1","0"],"scheme":"semver"}
{"type":"date","value":"2026-11-07","precision":"day"}
{"type":"interval","from":"2026-03-01","to":"2026-03-31"}
```

Rules (each tested):

- `$99/month` == `99 USD per month`; `$99/month` != `$99/month per seat`.
- `10 GB` == `10,000 MB` only under one declared convention (store it).
- `10%` != `10 percentage points`.
- `v2.1.0` != `v2.2.0`; `2.1` == `v2.1` only if scheme matches.
- Ambiguous `$` (no currency, no locale) → `ambiguous_currency`, **not** USD.
- No currency conversion without a dated rate. If a rate is needed and absent →
  `unverifiable/scope_mismatch`.

### 8.4 Freshness (separate from confidence)

```
F(c,t) = 0.5 ** (age / half_life(volatility_class))
next_check_at = last_verified_at + half_life / 2   (clamped to >= 1 h)
```

| volatility | half-life |
|---|---|
| ephemeral | 6 h |
| high | 24 h |
| moderate | 7 d |
| low | 30 d |
| stable | 180 d |

- Display `F` as a 0–100 **freshness index**, never as probability of truth.
- Display confidence separately: `strong | moderate | weak` derived from
  settlement + source authority + independence. No percentage unless calibrated.

---

## 9. DataGoblin reuse contract

| Reused | Exact call | Adaptation |
|---|---|---|
| `services/validator.py::settle` | `settle(cell, status=, quote=, source_text=, reason=)` | none — pure function |
| `schemas/evidence.py::locate_quote` | via `settle` and directly for display | none |
| `schemas/evidence.py::normalize_for_match` | base for subject/predicate comparison | extend for typed values |
| `services/pubdate.py` | publication-date parsing | add `published_date_source` |
| `services/temporal.py` | date-expression parsing | add explicit event-date extraction + quote |
| `services/deduper.py` | URL/content dedupe | canonical URL + content hash |
| `providers/crawl/fetcher.py` + `guard_url` + robots | crawler fallback | claim-scoped orchestration |
| `services/reducer.py::page_evidence_text` | chunk **selection only** | offsets always rebased to full `content` |
| `migrations/*` | conventions | new 014–020 |
| `repositories/*` | Postgres/JSONL parity pattern | new repo module for PS05 tables |

Not carried over as product surface: `runner.py`, `planner.py`,
`dataset_agent.py`, `discovery.py`, `gapfill.py`, `autofill.py`, the dataset UI.
They stay in the tree (tested) but are unreachable from the PS05 router.

---

## 10. API and events

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/verifications` | `{answer, prompt, generated_at, generated_at_source, source_urls, domain_filter}` → `{run_id}` |
| GET | `/api/verifications/{run_id}` | run + claims + counters |
| GET | `/api/verifications/{run_id}/events` | SSE, resumable via `Last-Event-ID` |
| POST | `/api/verifications/{run_id}/cancel` | cancel |
| GET | `/api/claims/{claim_id}` | claim + evidence + timeline |
| GET | `/api/claims/{claim_id}/history` | status history across runs |
| POST | `/api/claims/{claim_id}/reverify` | new run scoped to one claim |
| GET | `/api/verifications/{run_id}/report?format=md|json` | evidence pack |
| GET | `/api/gateway/status` | Bifrost health, budget, last calls |

Run stages:

```
queued → extracting_claims → planning_research → discovering_sources
      → extracting_evidence → settling_evidence → temporal_evaluation
      → completed | partial | failed | cancelled
```

SSE events (declared in one enum, asserted by a static test):

```
run.started  claims.extracted  claim.research.started  source.discovered
source.extracted  source.failed  evidence.located
evidence.settlement.completed  claim.status.updated
gateway.request  run.completed  run.partial  run.failed
```

Rules:

- Events are persisted with `sequence`; SSE replays from `Last-Event-ID`.
- One claim's Tavily failure never fails the run → `partial`.
- A failed fetch is `source.failed`, never silently dropped.
- `run.completed` counters must equal the claim rows, not a model's summary.

---

## 11. Bifrost integration (real, not a wrapper)

There is no Docker on the build machine, so the gateway runs as the Go binary the
npm package ships:

```powershell
.\scripts\bifrost_up.ps1            # start, import bifrost\config.json, verify the key
.\scripts\bifrost_up.ps1 -Stop      # stop the process this script started
.\scripts\bifrost_up.ps1 -Reset     # wipe the config store and re-import
```

`bifrost\config.json` is the committed configuration: `groq` as the primary
provider (its key is `env.GROQ_API_KEY`, read by the gateway, never by this
backend), `ollama` as the fallback (`env.OLLAMA_BASE_URL`), a `ps05-dev` virtual
key with `allow_all_providers` **and** explicit `provider_configs` for both, and
one routing rule whose fallback is `ollama/<model>`. The script probes liveness
on `/health` and authentication on `/v1/models`, because those are different
questions and conflating them reports a broken gateway as ready.

Day-1 exit condition, before any other model work:

1. Gateway up; `/v1/chat/completions` answers.
2. Two providers configured for real: primary `groq`, fallback `ollama`
   (local, free) **or** a second hosted key. A fallback that has never been
   exercised is not a fallback.
3. Virtual key `ps05-dev` with an allowed-model list and a USD budget.
4. Application calls only `http://127.0.0.1:8080/v1/chat/completions` with the
   virtual key.
5. Every response's request id, provider, model, tokens and latency are written
   to `model_calls`.
6. `GET /api/verify/gateway` proves it: base URL, whether the virtual key is set,
   budget and spend, per-stage models, a live health probe, aggregate stats and
   the last 25 calls. The run view shows the same rows.

Prove fallback honestly: temporarily set the primary model to an unavailable
provider and show the request still completes via the fallback, with
`fallback_attempts = 1` recorded. Keep that as a scripted demo step
(`scripts/bifrost_fallback_demo.py`).

Do not build a plugin. Do not call `groq_structured` from PS05 code.

### Token budget reality (measured today)

Groq free tier is **200,000 tokens/day**, not just 8,000 TPM. One live 30-page
DataGoblin run consumed ~198k. Therefore:

- A full 120-claim evaluation (~700–1,000 calls) **cannot** run on Groq free in
  one day.
- Mitigations, in order: (a) `model_calls` cache, (b) one call per claim that
  proposes relationships for all its candidate passages, (c) local Ollama for
  bulk evaluation runs, (d) a small paid budget if the user allows it.
- Tavily free credits are also finite (~1,000/month): cache `research_cache`,
  dedupe by canonical URL + content hash, 2 searches + ≤4 extracts per claim.

---

## 12. Tavily integration

1. `search(query, topic, depth, max_results)` → store `research_queries` rows +
   cache key. Capture `published_date` as **source metadata only**.
2. Select ≤4 URLs per claim by predicate-specific authority (official docs /
   pricing / changelog / regulator first).
3. `extract([urls])` → inspect `failed_results` even on HTTP 200. Record
   `source.failed`.
4. Store each success as a new `source_revisions` row (content hash). Same hash →
   reuse revision; different → new row. Never overwrite.
5. Crawler fallback: `guard_url` → robots check **before spend** → fetch →
   store as `extraction_method='crawler'`.
6. Never pass a search snippet to the relationship model as evidence.

Per-claim research budget: 2 queries (3 only if conflicting/incomplete),
≤4 extracted URLs, stop when the decision inputs are satisfied.

---

## 13. Frontend

Four screens, three-pane main workspace. No new state library; `fetch` +
`EventSource` + reducer, same patterns as the existing `useRunStream`.

| Route | Screen |
|---|---|
| `/` | Intake: big answer textarea, prompt, generation time (with explicit "unknown" toggle and the consequence explained), domain hints, Verify |
| `/v/:runId` | Workspace: left = answer with highlighted claim spans; centre = claim register (status, volatility, freshness, progress); right = evidence inspector |
| `/v/:runId/report` | Evidence pack preview + export (md/json) |
| `/watch` | Due claims, `next_check_at`, `Reverify now` |

Inspector must show, per claim:

- original span (click → scroll), normalized subject/predicate/value, type,
  volatility;
- evidence list: exact quote, source, publisher, **publication / event /
  retrieval** dates shown as three distinct rows, settlement result;
- timeline: answer generated → event date → publication → retrieval → now;
- deterministic rule id + reason code that produced the status;
- value diff for `changed` (old → new, effective date, quote).

Design language: graphite / warm-neutral, editorial, strong type hierarchy,
restrained status colors (teal / amber / muted red / grey). No giant circular
score. No card grid as the primary layout. Motion only to explain stage
transitions; respect `prefers-reduced-motion`.

Accessibility: keyboard-navigable claim register, focus visible, status never
communicated by color alone (icon + text), contrast ≥ 4.5:1, live region for run
progress.

Testing: node tests for the working-tree logic that already exists, plus
Playwright for: intake → running → completed, claim selection, inspector dates,
export, snapshot badge, failure path.

### As built (2026-11-10)

| Route | Screen |
|---|---|
| `/` | intake |
| `/runs` | run list, newest first |
| `/runs/:runId` | three-pane workspace (answer spans / claim register / evidence inspector), event rail, gateway panel, export |
| `/watchlist` | due claims and `Reverify now` |

The report is an export from the workspace (`GET /runs/{id}/report?format=md|json`),
not a separate route: a second page rendering the same data was one more surface
to keep truthful and no more evidence. The gateway panel is the frontend half of
§11's proof: per-call provider, model, tokens, cost, latency, fallback count and
failure badge, read from `model_calls`, with an honest empty state.

---

## 14. Evaluation (must start today, not on Nov 14)

### 14.1 Gold set

Target **120** claims (80 is the floor), annotated with evidence, in
`eval/gold.jsonl`:

```json
{"id":"g001","domain":"pricing","claim_text":"...","answer_generated_at":"2026-11-08T10:00:00+05:30",
 "gold_status":"changed","gold_reason_code":"value_changed_after_generation",
 "gold_original_value":{"type":"money","amount":"99","currency":"USD","period":"month"},
 "gold_replacement_value":{"type":"money","amount":"129","currency":"USD","period":"month"},
 "gold_event_date":"2026-11-07","gold_publication_date":"2026-11-09",
 "gold_sources":["https://..."],"gold_quote_spans":[{"url":"https://...","start":123,"end":201}],
 "gold_valid_from":"2026-11-07","gold_valid_until":null,"split":"dev|heldout"}
```

Distribution: supported 25, changed 25, contradicted 20, unverifiable-no-source
15, unverifiable-no-entailment 15, ambiguous temporal / conflicting 20.
Domains: API/versions, pricing/plans, leadership, law/policy, statistics,
stable historical controls. Hold out ~20% (fixed, untouched until the final run).

**As built (2026-11-10): 18 cases / 19 claims in `eval/gold.jsonl`, plus 3
real-entity cases in `eval/gold_live.jsonl`.** The set is built case-by-case
from the edge cases in §14.4 rather than padded to a count: every mandatory edge
case is a case, and the hermetic gate is 100% status accuracy on those, with zero
false `supported`, zero false `changed`, 100% quote validity and 100% event-date
accuracy where the case states dates. It is honest about what it is — a
regression gate for the decision table, not yet a measurement of generalisation.
The held-out split and the 80-claim floor are still open, and the number is not
quoted anywhere else in this repository. Do not describe this as a benchmark
until the set is big enough to be one.

The live set is deliberately small and names real, checkable entities (Notion's
founding year, Python's creator, and one product that does not exist). It runs
the full path — real Groq through Bifrost, real Tavily — and is gated on the
things the product controls: every stored quote resolves, every claim is
extracted, every run finishes, and no claim is called `supported` or `changed`
against the gold. Live status accuracy is printed but not gated, because the
gold describes the world at authoring time and a gate that fails when a price
moves would be measuring the news (D-014).

```powershell
backend\.venv\Scripts\python.exe eval\run_eval.py                                  # hermetic gate
backend\.venv\Scripts\python.exe eval\run_eval.py --live --cases eval\gold_live.jsonl
```

### 14.2 Metrics, in priority order

1. **False-supported rate** (most damaging).
2. False-changed rate.
3. Quote validity (every counted quote resolves, 100% required).
4. Event-date accuracy.
5. Change detection precision/recall.
6. Abstention accuracy.
7. Claim extraction precision/recall.
8. Macro-F1.
9. Latency and cost per claim (from `model_calls`).

### 14.3 Ablations

| Pipeline | Includes |
|---|---|
| Baseline | one LLM verdict from search results |
| Evidence-gated | + extraction, quote location, settlement |
| Full PS05 | + value normalization, event dates, decision table |

Run the baseline on a 40-claim subset if the token budget is tight; the full
pipeline runs on all 120 from cache.

**Status on 2026-11-10: not run.** The ablation needs a baseline verifier (one
prompt, no settlement) and the gold set is still 19 claims, so an ablation
number today would compare two things nobody should quote. It is sequenced after
the held-out split, and until then this section describes intent, not results.

### 14.4 Mandatory edge cases (each a test)

- Newer article describing an older event.
- Publication date but no event date.
- Replacement value with no effective date.
- Old contradiction followed by newer valid support.
- Two credible sources disagreeing.
- Quote mentions the claim but does not establish it.
- Same number, different currency or billing period.
- Different region / plan / version.
- Source changed since a previous snapshot (append-only proof).
- Tavily discovers a URL it cannot extract.
- Genuinely unverifiable claim.
- Answer with unknown generation timestamp.

---

## 15. Demo (designed before Nov 14)

Five claims: supported, changed, contradicted, unverifiable, temporal-abstention.
Four pre-run from stored snapshots (labelled `snapshot` + original retrieval
time). One live Tavily search.

Five-minute flow:

1. Paste answer + generation time.
2. Claims appear with spans highlighted.
3. Live claim: Tavily discovers sources, extraction + settlement visible.
4. Open `changed`: old → new value, event date ≠ publication date, exact quotes.
5. Open `unverifiable`: reason code and what would be needed to decide.
6. Gateway panel: Bifrost connected, provider, fallback attempts, tokens, cost.
7. Export evidence pack.

Failure path rehearsed: kill network → snapshot mode still completes, badged
`snapshot`; force a provider error → `source.failed` / `run.partial`, never a
fabricated verdict.

---

## 16. Schedule (dates are the contract, not the labels)

### 2026-11-09 (today) — foundations and the two irreversible dependencies

| ID | Task | Exit condition |
|---|---|---|
| D1-1 | DataGoblin: Groq **TPD** fix (§18) | 429 body parsed, rung latches, tests pass |
| D1-2 | Repo bootstrap (§4) | new repo, lineage docs, first commit pushed |
| D1-3 | Bifrost running + one completion + fallback proven | `model_calls` row with provider + request id |
| D1-4 | Migrations 014–020 applied; repo module for them | `psql` tables exist; backend imports |
| D1-5 | Gold-set schema + first 10 annotated claims | `eval/gold.jsonl` non-empty, reviewer-checked |

### 2026-11-10 — intake and claim intelligence

| ID | Task | Exit condition |
|---|---|---|
| D2-1 | `POST /api/verifications` + run/stage persistence | run row + SSE `run.started` |
| D2-2 | `claim_extractor` (strict JSON via Bifrost) | 12-claim answer → 12 claims with spans |
| D2-3 | `claim_normalizer` + volatility + type classification | opinion/prediction correctly excluded |
| D2-4 | Unknown `generated_at` path | `changed` blocked, UI explains |
| D2-5 | Intake screen + claim register skeleton | paste → claims visible |
| D2-6 | +20 gold claims | 30 annotated |

### 2026-11-11 — research

| ID | Task | Exit condition |
|---|---|---|
| D3-1 | `research_planner` (2–3 queries incl. prior value) | queries logged per claim |
| D3-2 | Tavily search + cache | `research_queries` + `research_cache` rows |
| D3-3 | Tavily extract + partial-failure handling | `source.failed` on a real failed URL |
| D3-4 | Source/revision storage + canonicalization | same URL twice → one source, hash-deduped revisions |
| D3-5 | Crawler fallback with `guard_url` + robots | blocked URL → recorded, no spend |
| D3-6 | Quote location against full stored text (rebased offsets) | offsets slice to the quote, test asserts |
| D3-7 | +25 gold claims | 55 annotated |

### 2026-11-12 — the graded core

| ID | Task | Exit condition |
|---|---|---|
| D4-1 | Relationship proposals (one call per claim, all passages) | proposed values stored |
| D4-2 | `settlement.py` via `validator.settle` | verified-only evidence; chokepoint test still green |
| D4-3 | `value_normalizer` + 12 comparison tests | all edge cases pass |
| D4-4 | `temporal.py` event date + its quote | `event_date_status` explicit/relative/missing |
| D4-5 | `decision.py` precedence + reason codes | 7 precedence + 8 `changed` unit tests |
| D4-6 | `claim_assertions` valid_from/valid_until | `changed` writes both, nothing else does |
| D4-7 | Freshness + `next_check_at` | shown per claim, never as truth % |
| D4-8 | +25 gold claims | 80 annotated |

### 2026-11-13 — product

| ID | Task | Exit condition |
|---|---|---|
| D5-1 | Three-pane workspace + span highlighting | full journey in browser |
| D5-2 | Evidence inspector: 3 dates, settlement, quotes | click claim → exact proof |
| D5-3 | Timeline + value diff | `changed` is self-explanatory |
| D5-4 | Durable SSE + reconnect | kill tab → resume from sequence |
| D5-5 | Gateway panel (`/api/gateway/status`) | provider/cost/fallback visible |
| D5-6 | Export md/json | file contains quotes, dates, rule version |
| D5-7 | `Reverify now` | new run, history appended |
| D5-8 | +25 gold claims | 105 annotated |

### 2026-11-14 — evaluation and demo

| ID | Task | Exit condition |
|---|---|---|
| D6-1 | Freeze gold set at 120 (or 80 floor) | held-out split sealed |
| D6-2 | Full benchmark + ablations | false-supported / false-changed reported |
| D6-3 | Snapshot mode (`CACHE_MODE=read-only`) | demo runs with network off |
| D6-4 | Pre-run the 4 demo claims | stored evidence, timestamps kept |
| D6-5 | Failure-path rehearsal | partial/failed shown honestly |
| D6-6 | Demo script frozen + 2 dry runs | under 6 minutes |

### 2026-11-15 — freeze and submit

| ID | Task | Exit condition |
|---|---|---|
| D7-1 | README, architecture diagram, benchmark methodology | judge can clone and run |
| D7-2 | `UPSTREAM.md`, `NOTICE`, license intact | lineage explicit |
| D7-3 | Remove/unlink dead routes, unused deps | no broken links in UI |
| D7-4 | Backend + frontend gates green | exact numbers quoted in README |
| D7-5 | Record demo | video + repo link submitted |

**Daily drift check (5 minutes):** does today's work map to a row in §13
capabilities and a task in this schedule? If not, cut it or log it in
`DECISIONS.md`. Does any non-negotiable in §1 have a test? If not, it is not done.

### State on 2026-11-10 (end of day 3)

Built and exercised:

- `backend/app/verify/` — claim extraction, query planning, settlement gate,
  value normalization, temporal fields, decision table, report builder,
  orchestrator with durable events. `backend/app/bifrost/client.py` is the only
  model interface; `backend/app/research/{tavily,sources,planner}.py` the only
  research path. Migrations 014–020 create schema `tev`; 021 removes the
  rule-version column default.
- `backend/app/api/verify.py` — create/list/get run, claim detail, events, SSE,
  report (md/json), watchlist, health, and `/gateway`. A run detail now also
  reports `gateway.replays`, counted from the rail, because a replayed run writes
  no `model_calls` row and an empty table would look like a bug.
- `frontend/src/verify/` — intake, run list, three-pane workspace, watchlist,
  gateway panel with a replay empty-state and the recorded date of each replay,
  export.
- `eval/run_eval.py` + `eval/gold.jsonl` + `eval/gold_live.jsonl` — two gates:
  hermetic for the rules, live for the whole path (D-014).
- `scripts/bifrost_up.ps1` + `bifrost/config.json` — the gateway starts without
  Docker, imports its config, and reports separately whether it is alive and
  whether the virtual key is accepted.

Six defects were found by running against real providers, and each has a test
that fails on the old code (D-013): a citation marker cannot refute; a
contradiction must name the claim's subject; `$10 per member per month` parses;
settlement shares the parser's vocabulary tables; R7 is reachable and a
scope-incompatible contradiction without support abstains; a transient Windows
file lock is not a model failure. `RULE_VERSION` is `tev-1.1.0` and is a module
constant, not an environment variable (D-015).

Verified by command, not by memory:

| command | last output |
|---|---|
| `pytest backend\tests -q` | 2313 passed, 8 skipped |
| `pytest backend\tests\verify -q` | 136 passed |
| `npm test` | 33 tests in 3 suites, 0 failures |
| `npm run build` | exit 0; 58 modules; `dist` JS 248.18 kB (78.21 kB gzip) |
| `eval\run_eval.py` (hermetic) | 18 cases / 19 claims; status 100%; false supported 0; false changed 0; 26 quotes valid; event dates 8/8; `$0.0000`; **Gate: PASS** |
| `eval\run_eval.py --live --cases eval\gold_live.jsonl --pause 20` | 3 cases / 3 claims; status accuracy 100%; 11/11 quotes valid; false supported 0; false changed 0; `$0.0000`; **Gate: PASS** |

Live end-to-end evidence, both runs on the local API:

- `1f64a7f8-52de-400a-831d-294dd160a93c`, `CACHE_MODE=off`: ended `partial`
  after Groq rate-limited real calls. It still recorded 8 audit rows (`ok` and
  `error`), 0 replays, and one honest `supported` verdict; a failed model call
  never became a verdict.
- `a139882f-abe6-494f-bcf9-1219bfea5c75`, `CACHE_MODE=read-only`: replayed the
  Notion answer under the final rules. Same four verdicts (3 `supported`,
  1 `contradicted`), 30 `model.snapshot` events, **0 gateway calls**, `$0`, and
  12/12 quotes resolving at their stored offsets. The extraction receipt was the
  one replay without its original timestamp; it now carries `created_at`, and
  `test_a_replayed_extraction_is_labelled_in_the_event_rail` asserts it.
  Re-running the same answer as `c0496df3-eba3-4baa-a4bb-135f5ad96055` replayed
  **30/30** results with their original timestamps, 0 gateway calls, `$0`, and
  12/12 quotes resolving: the fix is visible in the product, not only in a test.
- `5037ddbd-21da-48d6-839a-57998f25830f`, `CACHE_MODE=read-write`, a fresh
  answer the cache had never seen: the Eiffel Tower claim settled `supported`
  from a stored quote; the Brazil-language claim abstained
  `unverifiable/no_source` after 10 of the run's 13 model calls were rate-limited
  and recorded as `model.failed`. The run ended `partial`, 1/1 stored quote
  resolved, and the true claim was not called supported on evidence nobody read.

Not yet done, in order: a dedicated `DATABASE_URL`, so the Postgres adapter and
migration 021 can be exercised (the shared DataGoblin database is deliberately
untouched); the held-out split and the three ablations (§14.3); a larger gold set
(the current 18 hermetic + 3 live cases are a working gate, not the 105–120 in the
plan); the recorded demo (§15). Live evaluation is paced with `--pause 20`
because the Groq free tier answers 429 on bursts and Bifrost then falls back to
the 1B local model at 30–60 s per call. The product records the fallback or the
failure; it does not hide either.

---

## 17. Quality gate

```powershell
# backend (hermetic)
backend\.venv\Scripts\python.exe -m pytest backend\tests -q

# PS05-specific
backend\.venv\Scripts\python.exe -m pytest backend\tests\verify -q
backend\.venv\Scripts\python.exe -m pytest backend\tests\verify -q -m "not live"

# frontend
cd frontend; npm test; npm run build

# evaluation gate (hermetic: gold claims + scripted evidence, no keys)
backend\.venv\Scripts\python.exe eval\run_eval.py

# live gate (real Bifrost + Tavily; paced for the Groq free tier)
backend\.venv\Scripts\python.exe eval\run_eval.py --live --cases eval\gold_live.jsonl --pause 20
```

`eval\run_eval.py` (hermetic) exits non-zero when any absolute gate fails: 100%
status accuracy on the gold set, zero false `supported`, zero false `changed`,
every quote resolving at its recorded offsets, every stated event date matching.
`--live` gates on what the product controls — quote validity, no false
`supported`/`changed`, every claim extracted, every run finished — and prints
status accuracy without gating it, because the web is not deterministic (D-014).
Both modes printed `Gate: PASS` on 2026-11-10. The number is whatever the command
prints; do not quote a count that a later run can change.

Static guards to extend, not bypass:

- `test_promotion_chokepoint.py` — `settle` stays the only promoter.
- `test_event_contract.py` — every declared SSE event is emitted and vice versa.
- `test_migrations.py` — every `ALTER COLUMN` names a column that a
  `CREATE TABLE` actually created; migration 021 named the wrong tables once and
  only PostgreSQL would have noticed.
- New `test_no_direct_providers.py` — greps `app/verify`, `app/research`,
  `app/bifrost` for `groq`, `openai`, `gemini`, `anthropic` client imports and
  fails on any direct provider construction.
- New `test_no_publication_as_event.py` — fails if `event_date` is ever assigned
  from `published_date`.

---

## 18. DataGoblin Phase 0 fix (before the fork is seeded)

Measured today against the live account:

```
429 body: "... service tier `on_demand` on tokens per day (TPD):
           Limit 200000, Used 197983, Requested 2753. Please try again in 5m17.952s."
headers:  x-ratelimit-limit-tokens: 8000
          x-ratelimit-remaining-tokens: 8000
```

The TPM pacing is correct; the **daily** token budget is the binding limit, and
`classify.py` truncated the message before the word `tokens per day`. Five fixes:

1. `classify.py`: keep the full body in `details`; parse
   `(window, limit, used, requested, retry_after)`; stop slicing at 150 chars
   before the numbers.
2. `generate.py`: on a `tokens per day` 429, set `_GROQ_BLOCKED_UNTIL` and raise
   immediately — no 3× retry, no 70 s cap pretending it can honour 318 s.
3. While latched, `groq_structured` fails fast so the chain degrades in
   milliseconds; `structured_generate` falls through to the next rung.
4. Runner reports provider `rate_limited` + a `provider.exhausted` event with
   `reset_at`, instead of a bare page `error`.
5. `docs/33-BACKEND-ARCHITECTURE.md`: free tier is a 200k/day problem, not only
   8k TPM.

Tests: TPD 429 → exactly one attempt; latched second call → zero SDK calls;
chain falls through; `details.window == "tpd"`.

---

## 19. Risks and honest assessment

| Risk | Severity | Mitigation |
|---|---|---|
| **Groq TPD 200k/day** cannot run a 120-claim eval | High | cache `model_calls`; batch relationship calls; Ollama via Bifrost for bulk; 40-claim baseline subset |
| No second provider key for a real fallback | High | Ollama as the fallback provider — real, free, demonstrable |
| Tavily credits (~1,000/mo) | Medium | cache + canonical dedupe + 2 queries/claim |
| Event-date extraction is the hardest correct thing | High | own stage, own quote, `event_date_status`; abstain when missing |
| Tavily extract failures on judge day | Medium | snapshot mode + crawler fallback, both rehearsed |
| 6 days is genuinely short | High | Cut List applied top-down; core journey only |
| Scope creep from DataGoblin substrate | Medium | `PRODUCT.md` + unmounted legacy routes |
| PS05 still marked "proposed" | Medium | confirm with organizers; keep the repo submission-ready either way |

**Straight answer on feasibility:** the full wish-list is not achievable in six
days. The core journey — intake → atomic claims → Tavily research → settlement →
temporal decision → inspector → export, with Bifrost real and 80–120 claims
annotated — is achievable **only if** Day 1 ends with Bifrost working and the
gold-set schema started, and only if the rewrite, replay slider and watchlist
page are treated as cuts from the start.

---

## 20. Open decisions for the user

1. **GitHub origin**: exact repo name + namespace, and whether `gh` is
   authenticated for `gh repo create`.
2. **Provider keys available to Bifrost**: Groq only? OpenAI? Cerebras? Is
   Ollama installed locally (for a real fallback)?
3. **Tavily plan/credits** and whether a second key exists for evaluation.
4. **Paid model budget**: even $5 would remove the 200k/day ceiling for the
   evaluation. Yes/no.
5. **Deadline confirmation**: 2026-11-15, and whether DataGoblin itself must
   also be submitted.
6. **Rule 6** (§8.1): keep as written (`contradicted` on unknown dates) or
   switch to `unverifiable/missing_event_date`?

---

## 21. Submission checklist

- [ ] New repo, `upstream` documented, `NOTICE` + `UPSTREAM.md` + MIT intact
- [ ] `PRODUCT.md` matches the shipped product
- [ ] Every model call traverses Bifrost; no direct provider client in the verifier
- [ ] Bifrost budget + model allowlist configured; cost visible in UI
- [ ] Tavily Search **and** Extract used; partial failures visible
- [ ] Four timestamps stored and displayed separately
- [ ] `changed` impossible without a settled replacement + known event date >
      generation time
- [ ] Only `settle` grants counted evidence; chokepoint test green
- [ ] Reason codes for every abstention, shown to the user
- [ ] Evidence pack exports quotes, offsets, dates, rule version, usage
- [ ] 80–120 annotated claims, held-out split, metrics reported
- [ ] False-supported and false-changed rates in the README
- [ ] Snapshot mode reproduces the demo offline, badged as snapshot
- [ ] Backend + frontend gates green, exact numbers quoted
- [ ] Demo recorded, under 6 minutes
