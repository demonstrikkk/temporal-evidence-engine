# Upstream provenance

This repository was seeded from **DataGoblin**, an MIT-licensed evidence
extraction system.

| | |
|---|---|
| Upstream | https://github.com/demonstrikkk/datagoblin |
| Seeded commit | `8548fc5` |
| Seed date | 2026-11-09 |
| Relationship | fork; product surface diverged on purpose |
| Merge policy | **never** merge `upstream/main` wholesale; cherry-pick individual fixes |

## What was reused

| Reused | Where | Treatment |
|---|---|---|
| `validator.settle` / `adopt` | `backend/app/services/validator.py` | unchanged; the only promotion chokepoint |
| `locate_quote`, `normalize_for_match`, `parse_llm_json` | `backend/app/schemas/evidence.py` | reused; typed-value comparison added alongside |
| publication-date parsing | `backend/app/services/pubdate.py` | reused |
| temporal expression helpers | `backend/app/services/temporal.py` | reused; event-date extraction added |
| URL dedupe / canonicalisation | `backend/app/services/deduper.py` | reused |
| `guard_url`, robots, crawler | `backend/app/providers/crawl/` | reused as Exa/Tavily fallback |
| reducer chunking | `backend/app/services/reducer.py` | used for chunk *selection* only; offsets rebased to full text |
| migration conventions | `backend/migrations/` | new migrations 014+ in schema `tev` |
| Postgres/psycopg patterns | `backend/app/repositories/` | pattern reused by `app/verify/store.py` |
| test conventions, markers, static guards | `backend/tests/` | reused |

## What was written for this product

Atomic claim extraction, claim normalisation, typed value comparison, the
temporal decision engine, freshness, the Bifrost client, Tavily research
orchestration, source revisions, the verification orchestrator, the SSE event
store, the evaluation harness, and the entire verification workspace frontend.

## What was deliberately not carried over

`runner.py`, `planner.py`, `dataset_agent.py`, `discovery.py`, `gapfill.py`,
`autofill.py` and the dataset-first UI remain in the tree as tested substrate
but are unreachable from the product router. They are not the product.
