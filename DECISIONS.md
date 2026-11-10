# Decisions

Numbered, dated, and unemotional. If a rule in `docs/BUILD-PLAN.md` §8 changes,
bump `RULE_VERSION` and add an entry here.

## D-001 — Fork DataGoblin, do not rebuild the substrate (2026-11-09)

Reusing `validator.settle`, quote location, append-only revisions, URL guards and
robots checks saves 3–5 days of unscored work. The product surface (claims,
temporal decisions, research orchestration, workspace) is new. A from-scratch
build would buy architectural purity we cannot spend.

## D-002 — Postgres schema `tev`, not a second database (2026-11-09)

The seeded environment already has a Postgres DSN and the migration runner.
PS05 tables live in their own schema so they cannot collide with DataGoblin
tables, and can be dropped with `DROP SCHEMA tev CASCADE` without touching the
other product's data.

## D-003 — `settle` is called, never reimplemented (2026-11-09)

`app/verify/settlement.py` builds a one-field synthetic record and calls
`validator.settle`. A failed support does not become a contradiction; a
contradiction is settled on its own replacement value. This keeps
`test_promotion_chokepoint.py` meaningful in the new product.

## D-004 — The model never writes a status (2026-11-09)

Relationship proposals are strings on `claim_evidence`. `decision.py` assigns
`claims.status` from stored rows only. This is the difference between a verifier
and a summariser.

## D-005 — Four timestamps, four columns (2026-11-09)

No single `date` field. Unknown `event_date` is `missing` and blocks `changed`.
Publication date is never promoted to event date, enforced statically by
`test_no_publication_as_event.py`.

## D-006 — Cache is part of the contract (2026-11-09)

`model_calls` and `research_cache` make evaluation re-runs free, make snapshot
mode real, and keep a 200k-token/day provider budget from deciding whether the
product can be demonstrated. A replayed row is labelled `snapshot`.

## D-007 — Bifrost is a gateway, not an adapter (2026-11-09)

The backend speaks only `POST {BIFROST_BASE_URL}/v1/chat/completions` with a
virtual key. Provider keys live in Bifrost. `test_no_direct_providers.py`
enforces the boundary. A Python class named `Bifrost` would be a scored lie.

## D-008 — Decision rule 6 as written, for now (2026-11-09)

When support and contradiction coexist and a relevant event date is unknown, the
status is `contradicted` (BUILD-PLAN §8.1 rule 6). `unverifiable/missing_event_date`
is arguably more honest. Revisit only if the held-out set shows rule 6 is the
main error source; record the change here and bump `RULE_VERSION`.

## D-009 — Configuration is resolved without a working directory (2026-11-10)

This repository has no committed `.env`; secrets arrive from the process
environment or a local file that is never read into git. The upstream regression
guard assumed a `.env` existed and asserted its contents loaded from any CWD. It
now asserts the property that actually matters: the configured env-file path is
absolute and two `Settings()` instances built from different working directories
are identical. A missing env file is a valid configuration, not a failure.

## D-010 — Scope is compared before amounts (2026-11-10)

`compare("$99/month", "€129/month")` returned `different_amount`, because the
amount check ran first. `different_amount` is a real-difference reason, so a
different **currency** became a rule-5 replacement value and the claim was
reported `changed`. That is the most expensive error this product can make: a
claim refuted by nobody. Currency, period and per-unit basis are now compared
first; a mismatch in any of them is a scope reason and produces
`unverifiable/scope_mismatch` (or `ambiguous_currency` when `$` could be more
than one currency). `RULE_VERSION` is unchanged: R1–R7 and the `changed`
preconditions did not change, only which comparison result they receive.

The gold harness had the opposite defect and it cancelled the first one out of
view: the scripted gateway matched a relationship proposal against the whole
prompt, and the stored source text contains the first claim's sentence, so the
second claim on the same page was answered with the first claim's proposal. It
now reads the `CLAIM:` JSON line and matches the claim exactly, and raises if
the block is missing rather than answering `silent`.

## D-011 — The gateway's key format and its two health questions (2026-11-10)

Three findings from running `scripts/bifrost_up.ps1` against a real Bifrost
v2.5.0 on this machine, none of which the config file alone would reveal:

1. **A virtual key must look like a key.** `"value": "ps05-dev"` was accepted on
   import and then replaced by a generated `sk-bf-…` value, so every request
   carrying `ps05-dev` was refused with 401 while the config file still said
   otherwise. The committed key is now `sk-bf-tev-local-dev`, and the backend
   sends it on `x-bf-vk` as well as `Authorization`, because Bifrost documents
   that a key without the `sk-bf-` prefix is only read from `x-bf-vk`.
2. **`allow_all_providers` did not authorise the fallback model.** The key had
   `allow_all_providers: true` and the routing rule's fallback was still refused
   with `model_blocked`. The key now also carries explicit `provider_configs`
   for both providers with `allowed_models: ["*"]` and `key_ids: ["*"]`. Belt
   and braces, because a fallback that is blocked is a fallback that does not
   exist.
3. **Liveness and authentication are different questions.** The first version
   probed `/v1/models` and fell back to `/health`, which is unauthenticated; it
   therefore reported "Bifrost is up" while the key was being refused on every
   call. The script now waits on `/health` and then asks `/v1/models` with the
   key, reporting "key accepted, no provider could answer yet" separately from
   "virtual key refused". The backend's `health()` sends both headers for the
   same reason.

Also recorded: there is no Docker on this machine, so the gateway is the npm
package's Go binary (`%LOCALAPPDATA%\bifrost\v2.5.0\bin\bifrost-http.exe`). The
npm wrapper caches it as `bifrost-http.exe-0`; the script copies it to a stable
name. Starting the script through a PowerShell pipeline that captures its output
can hang after the gateway starts, because the child process inherits the pipe
handle — run it directly.

## D-012 - Three defects found by running the thing (2026-11-10)

The first live end-to-end run through the gateway (local Ollama, no Tavily key)
found three defects that no amount of reading the code had made obvious, because
each one is a case where the code did what it said and the words were wrong.

1. **`CACHE_MODE=off` silently disabled the audit trail and the budget.** The
   success path gated `add_model_call` on `cache_mode != "off"`, so a cache-off
   run recorded no rows. `gateway_stats` - which is what the budget check sums -
   then read `0` calls and `$0.00` spent, and the gateway panel showed an empty
   table while the gateway was answering every request. The audit row is not the
   cache: the row is now always written, and only `cacheable` follows the cache
   mode. A non-cacheable row is unreadable by `find_model_call`, which is exactly
   the guarantee `CACHE_MODE=off` is supposed to make.

2. **The cache was keyed on what the gateway decided, not on what we asked for.**
   The row stored `result.model` - the *resolved* model - while the lookup used
   the *requested* one. Once a fallback answered, the two strings differed and an
   identical request could never hit the cache. `CACHE_MODE=read-only` demo runs
   would therefore always raise `cache_miss`, and every "replay" would call the
   gateway again and spend. Migration 020 adds `resolved_model`; `model` is now
   the requested name and the cache key, `resolved_model` is the route that
   answered. The panel renders the resolved one, because printing the request as
   the answer is the one mistake a gateway panel must not make.

3. **A failed run announced itself as a partial one.** `_finish("failed", ...)`
   emitted `run.partial` with no error in the payload, so a model outage and a
   lost source looked identical in the event rail, and a run that extracted zero
   claims said "finished partially" with `0 reported failures`. Failed runs now
   emit `run.failed`, and the terminal payload carries `error` (code + message)
   for every terminal state. The UI names `no_claims` for what it is instead of
   claiming verdicts exist.

Regression protection: `tests/verify/test_bifrost_client.py` (cache off still
records; read-write replays under the requested key and reports the resolved
model; read-only replays and refuses to invent a call), plus two pipeline tests
for the terminal event and the replayed-extraction label.
