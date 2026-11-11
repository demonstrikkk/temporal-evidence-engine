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

## D-013 - The second live run: six defects between "settled" and "true" (2026-11-10)

The first run against real providers (Groq through Bifrost, Tavily for discovery;
run `e9a7b9e4-2014-4681-9093-d972eb28d7bd`) returned four verdicts. Two were
wrong, one crashed a claim, and the wrongness was never in the layer the unit
tests covered: `settle` did exactly what it was told with passages that were not
evidence. Each defect below is reproduced by a test that fails on the old code.

1. **A citation marker became a refutation.** The model returned `[2013]` as the
   quote that *contradicted* "Notion was founded in 2013". It located in the
   stored text at an exact offset, and its number matched the claim's value, so
   `settle` verified it: a footnote index outweighed two supporting sources.
   `settlement._is_statement` now refuses a quote with fewer than three words.
   That is a floor, not an entailment check, but a bracket, a bare number, a date
   or a heading cannot be a sentence about anything.
   Test: `test_a_citation_marker_is_not_a_statement`,
   `test_a_citation_marker_is_refused_for_being_a_fragment_not_a_wrong_subject`.

2. **A different plan's price refuted this plan's price.** "Business costs $24
   monthly" was proposed — and settled — as a contradiction of "Notion Plus costs
   $10 per member per month", because the value gate checks the amount, currency,
   period and unit and never checks *what the sentence is about*. Contradictions
   now name the claim's distinctive qualifier (`settlement.subject_named`,
   `must_name_subject=True`). Supports are exempt on purpose: a supporting
   sentence may say "it costs $10" in a paragraph that already named the subject.
   Matching is on word boundaries, so `pro` is not satisfied by `product`.
   Test: `test_a_contradiction_must_name_the_claim_subject`,
   `test_subject_matching_is_on_words_not_substrings`,
   `test_a_passage_about_another_plan_does_not_refute_this_one`.

3. **`$10 per member per month` was not a money value.** The tail parser split on
   the literal word `per`, so `member per month` was an unknown token, the parse
   failed, and the value stayed a string. A string value cannot use the currency,
   period or per-unit scope checks, which is the whole reason `changed` is
   trustworthy for prices. The tail is now read as a list of qualifiers, with a
   billing note (`billed annually`) not allowed to overwrite the price's period.
   Test: `test_a_per_unit_price_parses_in_the_order_people_write_it`,
   `test_a_billing_note_does_not_overwrite_the_price_period`.

4. **Settlement kept its own copy of the vocabulary.** Even after (3), every
   `per member` support was refused with "the quote does not state the same
   per-unit basis", because `settlement` had a second, hand-written `_UNIT_MARKS`
   table that had never heard of `member`. The mark tables are now derived from
   the parser tables in `value_normalizer` (`CURRENCY_MARKS`, `PERIOD_MARKS`,
   `UNIT_MARKS`), so adding a unit in one place cannot leave the other silently
   refusing it. Amount matching is on word boundaries for the same reason the
   subject match is: `10` is a substring of `$100`.
   Test: `test_a_per_member_price_support_settles_from_a_live_shaped_quote`,
   `test_a_longer_number_does_not_contain_the_stated_amount`.

5. **Rule 7 could never fire, and a scope difference could still refute.** Two
   independent defects in the same rule:
   - `_credibility` stripped every non-alphanumeric character from the host
     *before* splitting on the dot, so `acme.example` became `acmeexample`, the
     `"." in host` branch was dead, and no subject ever matched its own domain.
     R7 (`conflicting_evidence`) requires two primary sources, so it was
     unreachable from the product while its unit test passed.
   - A contradiction in a different currency, period or per-unit basis could
     produce a decisive `contradicted` whenever no support was found, because
     settlement only checks a proposal against its own quote. `decision.py` now
     reroutes the no-support case to `unverifiable/scope_mismatch`. The
     support-present case is left to R5, which already abstained.
   Tests: `test_first_party_credibility_is_read_from_the_domain_label`,
   `test_a_different_currency_does_not_refute_without_a_support`,
   gold cases `conflicting-primary-sources`, `scope-mismatch-without-support`.

6. **A transient Windows file lock was reported as a model failure.** A claim
   whose extraction had worked perfectly ended as `unverifiable/extraction_failed`
   because `os.replace` onto `state.json` hit `WinError 5` while another handle
   held the file. `LocalStore._save` retries with a unique temp file per attempt,
   `fsync`s, and logs rather than raises when every attempt fails: the in-memory
   state is still coherent and the next save persists it. Separately,
   `TEV_LOCAL_DIR` was resolved against the process working directory, so running
   the API from `backend/` and from the repository root produced two stores that
   each looked empty to the other. Relative paths are now anchored at the repo
   root (`config.REPO_ROOT`).
   Tests: `test_a_locked_state_file_is_retried_not_reported_as_a_claim_failure`,
   `test_a_state_file_that_stays_locked_does_not_take_the_run_down`.

**`RULE_VERSION` is now `tev-1.1.0`.** Two rules changed: a contradiction must
name its subject (2), and a scope-incompatible contradiction with no support is
`unverifiable/scope_mismatch` instead of `contradicted` (5). The R1-R7 table in
BUILD-PLAN 8.1 is unchanged otherwise, and the gold set grew from 12 cases to 18
so that both changes, the citation-marker refusal, the other-plan refusal and the
per-seat money parse are regression gates rather than intentions.

## D-014 - Two gates, because they answer two questions (2026-11-10)

The first live gold run (`--live --limit 3`) failed at 33% status accuracy and
the failures were not product defects: one run died on a Groq 429, and two cases
used invented entities (`Acme`, `Zorbiax`) whose facts no real search engine can
confirm. Using that file as a live gate would have taught us to lower a threshold
until the news agreed with us.

The two modes now measure different things and gate on different things:

| | hermetic | live |
|---|---|---|
| asks | is the decision/settlement layer correct? | does the whole path work against the real web? |
| data | `eval/gold.jsonl`, proposals from the file | `eval/gold_live.jsonl`, real entities, real Tavily + Bifrost |
| gates on | 100% status accuracy, false supported = 0, false changed = 0, quotes 100%, event dates 100% | quotes 100%, false supported = 0, false changed = 0, every claim extracted, every run finished |
| reports, does not gate | — | status accuracy, per-case outcomes |

Live accuracy is a measurement of the world, not a regression signal: a gold
case that says `Acme Pro costs $99` is a statement about 2026-11-10, and a gate
that reddens when the price moves is measuring the news. What live mode does
gate — a quote that does not resolve at its offsets, a claim the extractor
dropped, a run that ends `failed`, or a false decisive verdict — is entirely
inside the product, and stays at zero.

`eval/gold_live.jsonl` therefore contains only cases whose answers are stable and
widely republished, plus one product that does not exist (the honest abstention).
Contradiction and change paths are gated hermetically, where the source text is
fixed and the rule — not the web — is what is under test.

Known constraint: Groq's free tier answers 429 on token-per-minute bursts and
Bifrost falls back to local Ollama (`llama3.2:1b`), which works but takes 30-60 s
per call. A live run of three cases is therefore minutes, not seconds, and the
run records each fallback rather than hiding it.

## D-015 - `RULE_VERSION` is code, not configuration (2026-11-10)

D-013 changed two rules and bumped `RULE_VERSION` to `tev-1.1.0`. The running
product kept stamping `tev-1.0.0` on every verdict and on `/api/verify/health` —
not because the constant was wrong, but because `RULE_VERSION` was also a
`Settings` field and this machine's `.env` still pinned `tev-1.0.0`. An env file
silently outranked the code. The label a reviewer trusts to say *which rules
produced this verdict* could be changed without changing a rule: a comment that
can lie.

The fix is structural:

- `RULE_VERSION` is a module constant in `backend/app/core/config.py`; it is not
  in `Settings`, `.env`, or `.env.example`.
- migration `021_ps05_rule_version_no_default.sql` drops the column defaults, so
  the database cannot invent a version either; the writer must supply one.
- `backend/tests/verify/test_config_contract.py` fails if the value becomes a
  setting again, if it stops looking like a version, or if any module reads it
  from `settings`.

Evidence after the change: `/api/verify/health` reports `tev-1.1.0`, and the
read-only replay `a139882f-abe6-494f-bcf9-1219bfea5c75` stamps `claim.settled`
events with `tev-1.1.0`. The earlier replay `72379f1c-…` still reports
`tev-1.0.0`, because it was executed under those rules; historical stamps are
evidence and are not rewritten.

Known limit: migration 021 is written and reviewed but has not been applied to a
Postgres database in this environment — no dedicated `DATABASE_URL` exists yet,
and the shared DataGoblin database must not be migrated. The local store path is
verified.

## D-016 - The Postgres adapter had never been run (2026-11-10)

Everything above was verified through `LocalStore`. The Postgres adapter had
never touched a database: `DATABASE_URL` pointed at a shared DataGoblin project
that this product must not migrate, so `PERSISTENCE=postgres` was a code path
nobody had executed. It was not merely untested. It did not work.

A throwaway PostgreSQL 16.2 cluster on `127.0.0.1:5545` (nothing else; the shared
project was not touched) found four defects in order, one after another, each
visible only after the previous one was fixed:

1. **The migration chain could not start on a non-Supabase server.** Migration
   `001_core.sql` creates policies `TO anon, authenticated`, the roles every
   Supabase project has. Plain PostgreSQL has neither, so the chain aborted at
   the first file with `role "anon" does not exist`. `000_roles.sql` now creates
   `anon`, `authenticated` and `service_role` as `NOLOGIN` roles - names a policy
   can reference, not accounts anyone can connect as. On Supabase every branch is
   a no-op. Guard: `test_every_role_a_policy_grants_to_is_created_before_it_is_used`
   fails on the pre-fix tree.
2. **`POST /api/verify/runs` answered 500.** `PgStore._one/_all/_write` coerced
   parameters with `tuple(params)` before handing them to psycopg, so every
   statement written with named placeholders received a tuple of *keys*:
   `TypeError: named placeholders require a mapping of parameters`. No hermetic
   test could see it, because the local store has no SQL. Guard:
   `test_pg_store.py` drives the real method bodies against a cursor that
   enforces psycopg's own rule.
3. **`claims.extracted` was silently dropped.** The event payload carries the
   model receipt, the receipt carries `created_at`, and psycopg's JSON dumper
   refuses a `datetime`: `could not append run event ... Object of type datetime
   is not JSON serializable`. The local store kept the event because Python
   objects pass through; Postgres lost it. `_jsonb` now encodes
   datetime/date/time, `Decimal`, `UUID`, `Path` and sets, and both receipts use
   ISO text so the two stores record the same shape. Since no-silent-failure is a
   non-negotiable, a dropped event is a product defect, not a logging nit.
4. **The migration log could not show the tev tables.** `db_migrate.py` counted
   only `public`, so applying 014-021 printed an unchanged number and looked like
   a failure. It now reports both schemas.

Environment note: the minimal `pgserver` wheel ships only `plpgsql` and `vector`.
Migration 012 needs `pg_trgm`, which is contributed by every normal PostgreSQL
distribution (and by Supabase) but absent there. That is a deployment
requirement, not a code defect: `PERSISTENCE=postgres` needs a PostgreSQL with
contrib.

Evidence, all on an empty database:

| check | result |
|---|---|
| `python backend\scripts\db_migrate.py` from nothing | `000`-`021` applied, `migrate complete (13 public tables, 12 tev tables)` |
| re-run | same counts, no errors - the files are idempotent |
| `information_schema.columns` for `rule_version` | `verification_runs` and `claim_status_history`, both `column_default = <none>` |
| live verification through `PERSISTENCE=postgres` | see the run recorded in `docs/BUILD-PLAN.md` §16.1 |

The recipe is reproducible without Docker and without touching the shared
project; it is written down in `docs/RUNBOOK.md` so the next person can repeat it
rather than trust this paragraph.

## D-017 - The audit columns did not hold what their names said (2026-11-10)

Found by reading a row back out of Postgres, not by a test. `claim_evidence` has
two columns either side of the model's proposal:

```sql
relationship_settled  text,   -- schema says: supports|contradicts|mentions_only|silent
settlement_status     text,   -- schema says: verified|unverified
```

The writer stored `'settled'` / `'unsettled'` in the first and `'settled'` /
`'not_settled'` in the second. So a settlement word sat in a relationship column,
and the two columns mostly agreed on a word that described neither question. A
reviewer querying `relationship_settled = 'contradicts'` — the vocabulary the
schema itself documents — matched nothing, and the evidence pack printed
`— settled` where a reader expects to learn *what the source did*.

The columns now answer their own names:

| column | value | meaning |
|---|---|---|
| `relationship_settled` | `supports` / `contradicts` / `mentions_only` / `silent`, `NULL` | which relationship survived the gate |
| `settlement_status` | `accepted` / `rejected` | did the gate accept this proposal |

`verified` is deliberately not one of the values. In this codebase that word
belongs to `verification_status` — the cell-level promotion that only
`validator.settle` may grant — and reusing it for the fate of a proposal would
blur the one distinction `test_promotion_chokepoint.py` exists to protect.

Nothing about a verdict changes: `decision.py` reads in-memory facts and never
these columns. This is record fidelity. Migration `022` adds column comments
stating the vocabulary and rewrites no rows — an audit trail edited to match a
later vocabulary is no longer evidence of what happened. Rows written before
this decision keep the words they were written with.

The regression is pinned where the values are produced:
`test_pipeline.py` asserts an accepted row carries its relationship and a
rejected row carries `NULL`.

## D-018 - Budget mode is stored on the run and has one source of truth (2026-11-11)

The mode a run was created under was previously invisible after the fact, and the
query caps lived in two places: `planner.plan_queries` hard-coded 1/3/3 while
`resource_budget.get_policy` advertised 1/3/2. A UI that promised conserve would
starve research could therefore spend as much as balanced.

Now:

- `create_run` resolves an explicit per-request mode first, then the operator's
  current mode, and stores it in `tev.verification_runs.budget_mode` (migration
  `026`, additive, default `balanced`). A replay or an audit can see why a claim
  abstained.
- `planner` reads `budget_manager.get_policy(mode)["max_queries_per_claim"]` and
  `allow_live_search`, so the settings panel and the research path cannot drift.
  `deep` buys more than `balanced` (bounded by 6), `conserve` buys one query, and
  `offline` buys none.
- A budget mode still changes only how much research is purchased. It never
  lowers the evidence threshold: offline/conserve runs that cannot prove a claim
  abstain `unverifiable`, they do not promote it.
