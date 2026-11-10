# Temporal Evidence Engine — working agreement

Read this before changing anything. It is shorter than it looks, and every rule
here exists because it was already broken once.

## What this is

An AI-generated answer goes in with the time it was generated. Out comes a
claim-level verification: atomic claims, live research, exact quotes from
stored source revisions, **event dates separate from publication dates**, and a
deterministic verdict per claim — `supported`, `changed`, `contradicted` or
`unverifiable` — with a reason code and an evidence trail.

This repository was seeded from [DataGoblin](../datagoblin) (MIT). The evidence
substrate is reused; the product is not. See `UPSTREAM.md`.

## The quality gate

```powershell
# backend, hermetic (no network, no DB, no provider credits)
backend\.venv\Scripts\python.exe -m pytest backend\tests -q

# frontend
cd frontend; npm test; npm run build; cd ..
```

Markers are unchanged: `hermetic` (default), `postgres`, `live`, `slow`.
`--strict-markers` is on. Postgres tests read `DATABASE_URL` from the process
environment, never from `.env`.

Do not write a test count into a document. Write the command; the number is
whatever it printed.

## The six invariants

### 1. One function may grant `verified`

`app/services/validator.py::settle` is the only place a cell may leave
`verified`, and a cell needs two things: a support verdict **and** a locator
that resolves against the exact text the store holds.
`test_promotion_chokepoint.py` parses every module under `app/` and fails on a
literal `"verified"` written into a `verification_status` anywhere else.

The verifier calls `settle` through `app/verify/settlement.py`. It never writes
`verification_status` itself, and it never invents a seventh status.

A failed support is **not** a contradiction. A contradiction needs its own
synthetic cell, carrying the replacement value (or explicit negation), settled
against its own locatable quote.

### 2. Four timestamps, never merged

| field | means | lives on |
|---|---|---|
| `answer_generated_at` | when the answer was produced | `answers.generated_at` |
| `publication_date` | when the source was published/updated | `source_revisions.published_date` |
| `event_date` | when the underlying fact changed | `evidence_spans.event_date` |
| `retrieved_at` | when we fetched it | `source_revisions.retrieved_at` |

Unknown `event_date` is `missing`. It never falls back to
`publication_date`, and a newer publication date alone can never produce
`changed`. `test_no_publication_as_event.py` enforces this statically.

### 3. The model proposes; code decides

`relationship.py` proposes `supports | contradicts | mentions_only | silent`.
`settlement.py` decides whether that proposal counts. `decision.py` assigns the
status from stored rows. No model output is copied into `claims.status` — ever.

### 4. Every model call goes through Bifrost

`app/bifrost/client.py` is the only model interface. No module under
`app/verify/`, `app/research/` or `app/api/` may import a provider SDK.
`test_no_direct_providers.py` enforces it. Provider keys live in Bifrost config,
not in this backend.

Every call records: request id, provider actually used, model, input/output
tokens, cost, latency, fallback attempts, status. A model failure never
produces a verdict.

### 5. Tavily discovers; it never decides

Search returns candidates. Extract returns content that is stored as an
append-only `source_revisions` row. A search snippet is never evidence.
`failed_results` is inspected even on HTTP 200. A failed extraction is
`source.failed`, never a silent skip.

### 6. Store before you extract

A quote must cite a row that exists. Source revisions are append-only per
content hash: same hash reuses the row, different content gets a new row. Never
update stored text in place — that is how recorded offsets drift.

Offsets index into `source_revisions.content`, the **full** stored text.
Chunking for the model is a window with `(chunk_start, chunk_end)`; rebase
before insert. Never index into a reduced or truncated string.

## Migrations

`backend/migrations/NNN_*.sql`, applied in filename order by
`backend/scripts/db_migrate.py`, idempotent, additive, re-runnable, every new
column with a default. PS05 tables live in schema `tev` so they cannot collide
with the DataGoblin tables in the same database. Applied on startup when
`PERSISTENCE=postgres`.

If you write a column, something must read it.

## Caching is a guarantee, not an optimisation

`model_calls` is keyed by `(prompt_hash, model, stage)`; `research_cache` by
`hash(tool, query/url, options)`. `CACHE_MODE`:

| mode | behaviour |
|---|---|
| `read-write` | cache miss calls the network, response is stored |
| `read-only` | cache miss is an error; demo/snapshot runs |
| `off` | never read, never write |

A replayed result is labelled `snapshot` with its original timestamp. It is
never presented as live. This is what makes the evaluation re-runnable and the
demo survivable.

## No silent failure

Every run stage emits a durable event with a sequence number. A failed source, a
failed model call, a partial run and an abstention are all reported. `run.partial`
is a real outcome, not a euphemism.

## Drift control

- `PRODUCT.md` is the product contract. If a feature is not in it, it is scope
  creep, not ambition.
- `DECISIONS.md` records every rule change with a date. Any change to the
  decision table bumps `RULE_VERSION`.
- The Cut List in `docs/BUILD-PLAN.md` is applied top-down, never bottom-up.

## Before you claim something works

Say what you ran and what it printed. "The tests pass" is a claim about a
command; quote the number. A live run is only claimed if a live run happened.
