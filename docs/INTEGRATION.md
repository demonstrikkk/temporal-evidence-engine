# How it fits together

One page per claim, from "an AI answered this" to "here is what is still true,
and here is the sentence that proves it".

This document is the integration map. Every claim in it names the file that
does the work, and every number was printed by a command on 2026-11-11. Where
something is configured but cannot actually run on this machine, it says so.

```
answer + generated_at
   │
   ├─ extraction ......... Bifrost ─▶ Mistral ─▶ atomic claims
   │
   ├─ research ........... Tavily (discover) ─▶ candidate URLs
   │                       Tavily (extract)  ─▶ stored source_revisions
   │
   ├─ relationship ....... Bifrost ─▶ Mistral ─▶ supports|contradicts|
   │                                            mentions_only|silent
   │
   ├─ settlement ......... validator.settle  (the only gate that may
   │                       grant `verified`; one quote, one locator)
   │
   └─ decision ........... decision.decide    (R1–R7, code only)
                            │
                            └─▶ supported | changed | contradicted | unverifiable
```

---

## 1. Tavily — discovery only, never a verdict

`app/research/tavily.py` is the only network path to the web, and it is not
allowed to decide anything.

- **Discovery.** `TavilyClient.search()` returns `SearchHit`s with a URL, a
  title and a snippet. A snippet is never evidence. It exists to choose what to
  fetch.
- **Extraction.** `TavilyClient.extract()` asks Tavily for the page content. The
  content is stored as an append-only `tev.source_revisions` row keyed by
  content hash: the same hash reuses the row, different content gets a new one.
  Stored text is never updated in place, because every recorded offset indexes
  into it.
- **Failure is a row, not a silence.** Tavily returns `failed_results` alongside
  successful ones — including on HTTP 200. Both arrays are read, and a failed
  extraction emits `source.failed` and is counted in the run's failure totals.
  A run whose sources all failed is `run.partial`, never `completed`.
- **Budgeted.** `app/verify/resource_budget.py` caps queries per claim by
  `budget_mode`, stored on the run (`tev.verification_runs.budget_mode`,
  migration 026). Budget mode changes how much research is bought, never the
  evidence threshold for a verdict.
- **Cached.** `research_cache` is keyed by `hash(tool, query/url, options)`, so a
  replay of the same query is a stored response with its original timestamp and
  is labelled `snapshot`, never `live`.

Two rules hold it in place: a search result cannot appear in a claim's evidence
without a `source_revisions` row behind it, and settlement cannot resolve a
quote that is not in that row.

## 2. Bifrost — the only way to reach a model

`app/bifrost/client.py` is the single model interface. No module under
`app/verify/`, `app/research/` or `app/api/` may import a provider SDK, and
`test_no_direct_providers.py` parses the tree and fails if one does. There is
no `if provider == …` anywhere in the verifier: it asks for a model, the
gateway decides who answers.

**Configured** (`bifrost/config.json`, started by `scripts/bifrost_up.ps1`,
which imports the keys from the environment):

| provider | key source | role |
|---|---|---|
| `mistral` | `env.MISTRAL_API_KEY` | the model the product uses today |
| `groq` | `env.GROQ_API_KEY` | previous primary, still routable |
| `ollama` | local daemon | configured fallback for Groq models only |

One virtual key, `sk-bf-tev-local-dev`, bound to `127.0.0.1`. It is a gateway
credential, not a provider credential, and grants nothing outside that host.
`allow_direct_keys: false` means a request must present this key; a provider key
cannot be smuggled past the gateway.

Routing is **one rule per provider family**, because a single rule that pinned
`groq` as its target rewrote every request to Groq: a Mistral call was served by
the wrong vendor and nothing said so. Now `model.startsWith("groq/")` routes to
Groq and `model.startsWith("mistral/")` routes to Mistral with no fallback —
answering a Mistral judge prompt with a 1B local model would change the
verifier's brain mid-run without changing the record.

Stages pick their model through `BIFROST_MODEL_*`:

| stage | setting | current |
|---|---|---|
| `extract_claims` | `BIFROST_MODEL_EXTRACTOR` | `mistral/mistral-small-latest` |
| `propose_relationship` | `BIFROST_MODEL_JUDGE` | `mistral/mistral-small-latest` |
| `replacement_value` | `BIFROST_MODEL_JUDGE` | `mistral/mistral-small-latest` |
| `plan_research` | `BIFROST_MODEL_EXTRACTOR` | `mistral/mistral-small-latest` |
| `synthesize_answer` | `BIFROST_MODEL_SYNTHESIZER` | unset (chat surface only) |

`mistral-small-latest` is the compromise: `ministral-3b-latest` labelled a
supported claim `mentions_only`, and `zai-glm-5-3-flash` returns content as a
block list the parser cannot read. The demonstration path therefore does *not*
use the cheapest possible model — it uses the cheapest one that gets the
judgement right.

**Every call is a row.** `tev.model_calls` records request id, provider actually
used, requested model, resolved model, prompt hash, stage, tokens in/out, cost,
cost source, latency, fallback attempts and status. A model failure produces
`model.failed` with a kind (`transient`, `fatal`, `cache_miss`, `budget`) and
**never a verdict** — the claim abstains instead.

Three pieces of honesty live in that file:

- **Token pacing on real counts.** `_estimate_tokens()` sizes the request before
  it is sent, and `BIFROST_TPM_LIMIT` (60,000) plus `configured_tpm_limit()` in
  `scheduler.py` feed the process-wide `QuotaLimiter`. Without this the pacer
  believed the limit was 8,000 (Groq's) and slept between Mistral calls that
  were nowhere near any ceiling.
- **A provider's own JSON failure is transient.** `json_validate_failed` (a 400
  from the provider's structured-output mode) is retried rather than being
  reported as a broken verifier. It is reproducible on a trivial prompt, so it
  is the provider's bad draw, not our bad prompt.
- **Cost is not assumed to be zero.** Bifrost returns `cost: null` for Mistral
  while publishing `pricing.prompt = 1e-7`, `pricing.completion = 3e-7` in
  `GET /v1/models`. An unpriced completion is now priced from that catalogue and
  every row carries `cost_source` — `gateway` (a bill), `catalogue` (an
  estimate), `none` (unpriced). A `$0.00` panel now means "free", not "unknown".

## 3. DataGoblin — what was reused, and what was not

The repository was seeded from DataGoblin (MIT, see `UPSTREAM.md`). The evidence
substrate is reused; the product is not.

**Reused, and load-bearing:**

- `app/services/validator.py::settle` — the one function allowed to grant
  `verified`. A claim assertion is projected into a synthetic dataset cell and
  settled by the same function the rest of the substrate uses, so the verifier
  does not carry a second, weaker gate. `test_promotion_chokepoint.py` parses
  every module under `app/` and fails on a literal `"verified"` written into a
  `verification_status` anywhere else.
- The parser and normalisation vocabulary tables (`app/verify/value_normalizer.py`
  shares the substrate's tables), so `$10 per member per month` parses the same
  way in settlement as it does in extraction.
- The FastAPI application shell, structured logging, cache coordinator and
  governance/quota plumbing (`app/governance/quota_ledger.py`).
- The Postgres adapter pattern and migration runner (`scripts/db_migrate.py`).

**Not reused:** DataGoblin's chat/CLI surface and its own tables. PS05 tables live
in schema **`tev`** (D-002), so this application and DataGoblin can share one
database without either seeing the other's rows. 28 migrations, filename order,
idempotent and additive, applied on startup when `PERSISTENCE=postgres`.

Sixteen tables carry the product: `answers`, `claims`, `claim_assertions`,
`claim_evidence`, `claim_status_history`, `evidence_spans`, `sources`,
`source_revisions`, `verification_runs`, `verification_events`, `model_calls`,
`research_cache`, `cache_entries`, `conversations`, `messages`, `quota_ledger`.

## 4. The verifier — where a probabilistic proposal becomes a decided status

Two gates, in this order, and both are code.

**Settlement** (`app/verify/settlement.py`) asks whether the quote, sliced from
the *stored* text, actually establishes the value it is offered for. It checks
the quote is locatable at its offsets, that it is a statement rather than a
citation marker or a fragment, that it mentions the value, and — for a
contradiction — that it names the claim's subject and, when the claim's value is
text, that it is about the same property at all. A failed support is **not** a
contradiction: it means "this passage does not establish the claim".

**The decision table** (`app/verify/decision.py`, R1–R7) assigns the status from
stored rows only. The model's proposal is an input; it is never the answer, and
no model output is copied into `claims.status`.

The scope gate runs **before** every rule, and it filters rather than guards one
branch (D-020, `tev-1.2.0`): a contradiction whose comparable value differs in
currency, period or per-unit basis leaves the contradiction set before R1 is
evaluated, so R3/R5/R6/R7 cannot be reached by it. A compound candidate is split
into clauses first, and the gate blocks only when every comparable clause is
incomparable. A blocked passage is disclosed in
`Decision.material["scope_blocked"]` and in the `claim.settled` event; it never
changes the status by itself.

`changed` is the narrowest path and needs all eight conditions (BUILD-PLAN §8.1):
support at generation time, a replacement that is the same subject/predicate/scope,
a locatable quote, a settled value, a **known and quoted** event date, that event
date later than `answer_generated_at`, evidence that it became effective, and no
later reversal. Four timestamps stay four columns — `answer_generated_at`,
`publication_date`, `event_date`, `retrieved_at` — and an unknown event date is
`missing`, never a fallback to publication. `test_no_publication_as_event.py`
enforces that statically.

## 5. Caching is a guarantee, not an optimisation

Layers exist because a replay must be possible and a demo must survive a dead
provider:

| layer | what | key |
|---|---|---|
| L1 | deterministic computations | operation + input hash |
| L2 | completions | `(prompt_hash, model, stage)` in `tev.model_calls` |
| L3 | Tavily search | `hash(tool, query, options)` in `tev.research_cache` |
| L4 | page snapshots | content hash of the stored revision |
| L5 | claim→evidence reuse | claim fingerprint + rule version |

`CACHE_MODE` is a contract, not a hint:

| mode | behaviour |
|---|---|
| `read-write` | a miss calls the network, the response is stored |
| `read-only` | a miss is an error; used for demo and snapshot runs |
| `off` | never read, never write |

A replayed result is `status="snapshot"` with its original `created_at`, and it
is never presented as live. The audit row is written even when the cache is
`off` — otherwise the budget check, which sums `model_calls`, would read zero
while money was being spent (a real defect, fixed).

## 6. The surface

API (`app/api/verify.py`): `POST /runs`, `GET /runs`, `GET /runs/{id}`,
`GET /runs/{id}/claims/{claim_id}`, `GET /runs/{id}/events`,
`GET /runs/{id}/stream` (SSE), `GET /runs/{id}/report?format=md|json`,
`GET /watchlist`, `GET /gateway`, `GET /health`.
Governance (`app/api/governance.py`): `GET /quota`, `GET /cache/metrics`,
`POST /cache/invalidate-config`, `POST /budget-mode`,
`GET /bifrost/diagnostic`.

Frontend: `/runs`, `/runs/:runId` (answer spans / claim register / evidence
inspector, event rail, gateway panel), `/watchlist`. The report is an export
from the workspace, not a second page rendering the same data.

Every stage emits a durable `tev.verification_events` row with a sequence
number, which is what the event rail streams and what makes `run.partial` a
claim you can check rather than a euphemism.

## 7. What was measured, and what it means

Hermetic gold set (`python eval/run_eval.py`, 2026-11-11, rule `tev-1.2.0`):

- 106 cases / 107 claims, 84 dev / 22 held out
- status accuracy **100.0%**
- false `supported` **0**, false `changed` **0**
- abstention recall **100.0%**, over-abstention **0**
- quote validity **138/138**, event-date accuracy **44/44**

Ablations (`python eval/ablate.py`, identical proposals in every arm):

| arm | status accuracy | false supported | false changed | abstention recall |
|---|---|---|---|---|
| `baseline` | 57.9% | 14 | 16 | 38.9% |
| `baseline-collapsed` | 53.3% | 14 | 0 | 38.9% |
| `evidence-gated` | 65.4% | 6 | 16 | 61.1% |
| `full` | 100.0% | 0 | 0 | 100.0% |

The 100% is a statement about the deterministic layer reproducing its own
documented decisions on new instances. It is not a generalisation benchmark: the
ablations hold the model's proposals constant, so they say nothing about whether
a real model would propose the same relationships.

Live path, last full run under `tev-1.2.0`
(`86ccc5d3-0bf5-4263-b1ab-dc97e16dc7ea`, empty store, `CACHE_MODE=read-write`):
`completed` in 83.8 s, 15 gateway calls, 37,669 input / 1,318 output tokens,
**0 model failures, 0 search failures, 0 `run.partial`**, both claims
`supported/supported_by_source`. That run printed `cost: 0.0` because Bifrost
returned `cost: null` — at the catalogue prices it spent about **$0.0042**
(0.42 cents). That printed zero is the defect the `cost_source` column now
prevents.

Backend gate: `backend\.venv\Scripts\python.exe -m pytest backend\tests -q` →
**2483 passed, 16 skipped** in 212.27 s.

## 8. What is configured but does not run here

Said plainly, because a claim of integration that skips these is a lie:

- **Postgres is not the demo path.** No dedicated hosted `DATABASE_URL`, so the
  product runs `PERSISTENCE=local` (one `state.json`, 18 collections). The
  Postgres adapter was verified end to end against a throwaway cluster on this
  machine (D-016), and the 28 migrations applied there in order. The shared
  Supabase/DataGoblin project is deliberately never migrated.
- **The Ollama fallback cannot fire.** Ollama is not usable on this laptop, and
  the gateway started with 67 models and no fallback host. `bifrost_up.ps1`
  reports this and continues; it is warn-only by design. The fallback chain is
  configuration, not a demonstrated capability.
- **`changed` is not in the recorded demo answer.** The demo answer has no
  later page stating a dated replacement after `generated_at`, so it ends
  `supported`. The `changed` path is proven by 16 gold cases and by the
  ablation's ability to lose precisely those 16.
- **No demo video.** The assistant cannot record one.
- **The live gold set was not re-run after D-020.** It costs provider credits and
  the answer depends on what the web said that morning; the hermetic gate is the
  regression gate (D-014), and the live path was exercised by the two-claim run
  above instead.

## 9. Commands

```powershell
# backend, hermetic: no network, no DB, no provider credits
backend\.venv\Scripts\python.exe -m pytest backend\tests -q

# gold set (hermetic gate) and the sealed quarter
backend\.venv\Scripts\python.exe eval\run_eval.py
backend\.venv\Scripts\python.exe eval\run_eval.py --split heldout

# ablations
backend\.venv\Scripts\python.exe eval\ablate.py

# gateway up (imports the provider keys, prints what it could and could not do)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bifrost_up.ps1

# frontend
cd frontend; npm test; npm run build
```
