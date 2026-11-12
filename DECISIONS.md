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

## D-019 - The generated gold cases are instances, and the ablations share proposals (2026-11-11)

The gold set was 18 hand-written cases. A hand set cannot be held out: every case
was added because it shaped a rule, so scoring on it measures memory, not
verification. D6-1 and D6-2 needed a split and an ablation, so the set had to
grow. Two options: write 80 more cases by hand, or parameterise the rules the
hand cases already encode.

Parameterising won because it is honest about what it measures:

- `eval/generate_cases.py` owns no rules. It re-parameterises the hand cases over
  different entities, values and dates, and every expectation is the documented
  outcome of a rule. If a template is wrong the hermetic gate fails; the template
  does not become the answer key.
- The held-out quarter is positional (every fourth generated case in a
  fixed-order file), so the split is reproducible and was sealed before the final
  run. The hand cases are `dev` by definition.
- The generated number is therefore a reproduction check on new instances, not a
  generalisation benchmark. Both documents say so where the number is quoted.

For the ablations, every arm is given the same model proposals from the gold
file. That is deliberate: it holds extraction and model quality constant and
isolates the deterministic layer, which is what the project claims. It also means
the ablation cannot say whether a real model would propose the same
relationships, and §14.3 of the build plan states that limitation next to the
numbers.

Two baselines are reported, `baseline` and `baseline-collapsed`, because choosing
only the variant that scores worse is how an ablation becomes a sales document.
`baseline` reads the newest page first - what a search-plus-model pipeline
actually does - and gets every `changed` case right. Its failures are the point:
publication time read as event time, a differently scoped number read as a
replacement, and two first-party pages read as agreement.

## D-020 - The scope gate is a filter in front of every rule, not a guard on R2b (2026-11-11)

Two live runs produced two false refutations of claims that four sources
supported. Each was a different defect, and neither was a settlement defect:

- **The compound candidate.** A source wrote "Notion Plus costs $8 per user per
  month (annual billing) or $10 per user per month (monthly billing)". The
  model's `candidate_value` was that whole sentence, `normalize_value` called it
  a `string`, and `compare` answered `type_mismatch`. The old gate demanded a
  comparable type on *both* sides, so it declined to act - and R5 read the
  passage as a replacement price and answered `changed`.
- **The different property.** "The Notion free plan lets an individual store
  unlimited pages and blocks" was "refuted" by "Free plans have a 5 MB file
  upload limit per file". Both values are text, the subject ("free plan") is
  named, the quote resolves at its offsets, so `settle` accepted it. `compare`
  could only say `different_text`, which is exactly what a genuine textual
  refutation looks like.

The rule changes:

1. **The gate filters, it does not guard one branch.** A contradiction the gate
   blocks leaves the contradiction set *before* any rule runs, so R3, R5, R6 and
   R7 cannot be reached by it. Previously only R2b consulted the block list, so a
   blocked contradiction still drove R5 to `changed`. This is the change that
   made R2b's `scope_mismatch` abstention unreachable for a supported claim: the
   claim is now `supported`, and the blocked passage is disclosed in
   `Decision.material["scope_blocked"]` and in the `claim.settled` event. R2b
   still fires when a scoped contradiction exists and nothing settles the claim.
2. **A compound candidate is judged clause by clause.** `_comparable_clauses`
   splits on `or`, `;` and `/`, strips parentheticals (which the normalizer treats
   as text, so `$8 per user per month (annual billing)` does not normalise), and
   keeps the clauses that are comparable. The gate blocks only when *every*
   comparable clause is incomparable; one genuinely different clause still
   refutes.
3. **A textual claim needs a shared property term.** `object_terms_named` in
   `settlement.py`, reached through `settle_assertion(object_text=...)` from both
   contradiction branches in the orchestrator. It applies **only** when the
   claim's own value normalises to `string`. A money, quantity, period or version
   is compared field by field, and this check would reject a real refutation
   phrased differently ("headquartered in Munich" for "located in Berlin").

`RULE_VERSION` is `tev-1.2.0`.

Measured, in this order:

- Replay of run `9d98e100` (read-only, same store copy): "Notion Plus costs $10
  per member per month" moved from `contradicted/temporal_ambiguity` to
  `supported/supported_by_source`.
- A fresh live run with an empty store, `CACHE_MODE=read-write`, rule
  `tev-1.2.0`, run `86ccc5d3-0bf5-4263-b1ab-dc97e16dc7ea`: `completed` in 83.8 s,
  15 gateway calls, 37,669 input / 1,318 output tokens, 0 model failures, 0
  search failures, 0 `run.partial`, and **both** claims
  `supported/supported_by_source`.
- Hermetic gold set: 106 cases, 107 claims, 100.0% status accuracy, 0 false
  `supported`, 0 false `changed`, 100% abstention recall, 138/138 quotes,
  44/44 event dates.
- Ablations unchanged at 100.0 / 65.4 / 57.9 / 53.3.

Consequences that are deliberate, not accidental:

- Nine gold expectations moved from `unverifiable/scope_mismatch` to
  `supported/supported_by_source` (`scope-support-*`, plus the hand-written
  `scope-mismatch`). They were written for the old rule; under this one a source
  settles the claim and a differently-scoped passage cannot veto it. The reason
  code keeps coverage through `scope-only-*`, which has no support to settle with.
  This is a recorded rule change, not a test loosened to get a green run: the
  expectations were regenerated from the decision table, across dev **and**
  held-out instances alike.
- The hand-written `scope-mismatch` case also held a double-encoded euro (UTF-8
  bytes read as cp1252 and re-encoded), so the normalizer saw text rather than
  money and the gate could not fire at all. The data was repaired; the case now
  exercises the rule rather than the corruption.

What this does not claim: `object_terms_named` is a term-overlap heuristic, not
an entailment check. It refuses a contradiction that shares no distinctive word
with the claim's stated property; it cannot tell whether a passage that *does*
share a word actually addresses the property. It is a floor on absurd
refutations, and the reason it is confined to textual values is that numbers do
not need it.

## D-021 - Coverage is a record beside the claim, never a claim status (2026-11-11)

The Eiffel paragraph produced two claims from roughly seven assertions and
nothing anywhere said five were skipped. The tempting fix is a sixth status
(`not_checked`, `subjective`), and it is the wrong one: it would break the
four-status verdict contract, put an extraction fact in a column that means
"what the evidence established", and give `validator.settle` a second value it
does not own.

So coverage lives in its own tables:

- `tev.assertion_spans` - one row per checkable value found by the deterministic
  scanner, with the claim that covers it (or `NULL`) and a disposition;
- `tev.coverage_audits` - one row per run with the counts and ratios.

`claims.status` keeps exactly `pending | supported | changed | contradicted |
unverifiable`. The coverage dispositions are `checked | subjective |
prediction | not_checked`, and they are not verdicts. `test_coverage.py` pins
that the audit has no status field to write.

## D-022 - Coverage is deterministic, and every assertion gets a row or a reason (2026-11-11)

The audit does not ask a model what the answer asserted:

1. split the answer into sentences with absolute offsets (`segment.py`);
2. find money, percentages, measures, dates, versions and bare quantities with
   ordered regexes, longest match first (`assertions.py`);
3. a candidate is `checked` when a stored claim span **contains** it;
4. a claim that contains no candidate is still an assertion in the
   denominator - otherwise ten claims from one sentence would report 100%;
5. anything left is given `subjective`, `prediction`, or the admission
   `not_checked` from the sentence's own words.

`coverage` is checked / every assertion. `material_coverage` removes opinions
and predictions from the denominator, because declining to verify a preference
is not an incomplete run. Both are reported; neither is a gate.

The extraction drop paths now name themselves. `postprocess(drops=...)` records
every proposal that did not become a claim with a code (`not_locatable`,
`non_verifiable:opinion`, `duplicate`, `claim_limit`, ...), and the orchestrator
emits a `claim.dropped` event for each. "The model returned four and we stored
two" is only auditable if the other two are named.

## D-023 - One capped repair pass; a recovered claim is a first-class claim (2026-11-11)

When the audit leaves `not_checked` values, the run may make **one** focused
re-extraction over those sentences (`COVERAGE_MAX_REPAIR_CALLS`, default 1;
`COVERAGE_MAX_TARGETS`, default 12). It is disabled in `offline` mode. The
repair:

- is a normal Bifrost call (`stage="repair_coverage"`), cached and priced like
  every other call;
- is restricted back to the target windows by `span_ranges`, so it cannot
  become a second full extraction;
- drops duplicates of claims already stored (`duplicate_existing`), proposals
  already covered (`already_covered`), and anything past the claim ceiling;
- stores accepted claims with `claims.extracted_by='coverage_repair'` and then
  verifies them through the same research and settlement path as the first
  pass.

A failed repair is a `coverage.repair_failed` event and an unchanged number,
never a quietly improved one. A coverage failure cannot fail the run: the
verification of the claims already extracted is the product, and the audit is
the lens on it.

## D-024 - The disposition vocabulary is closed (2026-11-11)

`checked`, `subjective`, `prediction`, `not_checked` are the only values
`tev.assertion_spans.disposition` may hold. Adding a fifth needs a migration and
a decision here, for the same reason a sixth claim status would: a vocabulary
that grows by accident stops being checkable.

Measured after the change:

- `backend\.venv\Scripts\python.exe -m pytest backend\tests -q` ->
  **2517 passed, 16 skipped** in 191.50 s;
- hermetic gold -> 106 cases / 107 claims, 100.0% status accuracy, 0 false
  supported, 0 false changed, 138/138 quotes, 44/44 event dates, **Gate PASS**;
- `frontend npm test` and `npm run build` pass.

## D-025 - The trace is a projection of the audit rail, not a second account of the run (2026-11-11)

`verification_events` answers "what happened, in order". It cannot answer
"which stage spent the money": a rail row has no parent, no duration and no
price. Adding those to the rail would change what the rail is, so they live in
`tev.execution_traces` (one header per run) and `tev.trace_spans` (one row per
operation) — the same run, seen as a tree.

The risk of two tables describing one run is that they diverge. Five rules
close that, and each of them is a choice rather than an accident:

- **One writer.** A span is closed only by `TraceRecorder.end(span, event, …)`,
  and `event` is the row `Verifier.emit` returned. `trace_spans.event_id` is
  that row's id. A span cannot be written without being closed, and a span with
  no rail row stores NULL rather than borrowing someone else's id.
- **`event_id` is `text` with no foreign key.** The local store ids events with
  a UUID; the Postgres store uses a `bigserial`. A foreign key would force one
  adapter to misdescribe its own identifiers. What the column promises — that
  the id names a row the rail actually holds — is enforced by
  `test_every_span_names_a_real_audit_event`, which compares against the run's
  events rather than against a type.
- **`ordinal` is the order spans were begun in, not a timestamp.** Clock
  resolution and UUID ordering cannot reproduce a tree; an integer can.
- **`claim_id` is on the span.** "What did this claim cost" is then a query.
  Reconstructing it from the event payload's jsonb is precisely the by-hand work
  this work package exists to remove.
- **`attempt` is our attempt number.** A provider fallback inside Bifrost stays
  on `model_calls.fallback_attempts`; turning one request into several spans
  would multiply a single call into a fan-out that never happened.

Two failure modes are handled as defects rather than as gaps. `end` is
idempotent per span, so closing twice cannot write two rows and double a cost;
`finish` closes any span still open as `error_type='unclosed'`, because an
operation that began and never reported is exactly the silence this repository
exists to remove. A trace write that raises emits `trace.failed` once and the
verification continues: losing the trace is bad, losing the verdict because the
trace failed is worse.

**Replay determinism is structural.** `fingerprint()` compares each span's
ordinal, its parent's ordinal, stage, operation, attempt, tool, prompt version,
error type and cache state. Ids, timings and token counts are excluded because
they are measurements of one execution, not the shape of the run. A second run
against a warm store is *expected* to differ — `reuse_evidence` replaces
`search` — and the test pins that difference instead of pretending the two
trees are identical.

**Cost is split, never summed.** `cost_source` already distinguishes a gateway
bill from a catalogue estimate from no price at all (migration 027), so
`summarise()` reports `gateway_usd`, `estimated_usd` and `unpriced_spans`
separately, plus the span counts that make the distinction meaningful: a
gateway that reports `0.0` did price its calls, and that zero is a fact; a span
with `cost_source='none'` has no price, and printing `$0` for it would make an
unpriced run look free. Stage latency is summed and must never be read as
elapsed time — nested spans overlap by construction.

`TRACE_ENABLED` defaults to true. The switch exists for bulk harnesses that will
not read the tree; a run with tracing off has no tree at all, and the API says
so rather than returning an empty one.

The UI keeps the event rail as the spine and renders the tree beneath it. The
filter that shows model calls keys off `prompt_version` rather than an operation
name, so the next stage that calls a model is included without anyone
remembering to add it, and a filtered view keeps the ancestors of what it
matched — marked as context — so a row is never shown under a parent that is
not on screen.

The run report prints the same split, because that is the document people quote
afterwards. A stage with no priced span prints an em dash, never `$0.0000`:
"nobody priced this" and "this was free" are different claims, and only the
first is known. A run with no trace says cost cannot be attributed by stage and
still prints the gateway total, which does not need the trace to exist. The
stage table deliberately has no elapsed-time column: nested spans overlap, so a
sum of their durations is not the time the run took.

## D-026 - A prompt version is a claim about text, so the text is registered (2026-11-11)

`trace_spans.prompt_version` exists because a hash can tell you two calls differ
but never that one is the *second* version of the same instruction. A
hand-written version label has exactly one failure mode: someone edits the
prompt and does not bump it, and every span afterwards vouches for text it never
ran.

So the prompts are module constants (`_SYSTEM`, `_TEMPLATE`) rather than
f-strings built at the call site, and `app/verify/prompts.py` records the
SHA-256 prefix of each version's exact text. `test_trace.py` recomputes the
digest for every registered version and compares. Editing a prompt without
bumping its version fails, and bumping a version without registering its text
fails in the other direction — a missing entry is the same failure as a stale
one. A second test reads the orchestrator's `prompt_version=` sites and requires
every one of them to name a registered version, so a new call site cannot
quietly run unlabelled.

The digest covers the system instruction and the message template. It does not
cover the interpolated answer, the claim or the document windows: that is data,
and it is what `trace_spans.prompt_hash` records separately.


## D-027 - One research plan per subject group, researched once (2026-11-11)

A five-claim answer about one product issued five searches for "the product's
price" and extracted the pricing page five times. The research plan is the row
that says which questions were asked once, for which claims, and what the
pre-filter then refused to send.

### Grouping key

Claims are grouped by normalised `subject` (case-folded, whitespace collapsed).
That is what makes two questions the same question: "Acme Pro price" and "Acme
Pro costs" are one page and one search, not two of each. Claims with no usable
subject are not grouped with each other - the only thing two such claims share
is that nobody said what they are about - so each gets a singleton plan keyed
`claim:<id>`.

### Query union

A group plan holds the deduplicated, case-insensitive union of its members'
per-claim queries, in claim order, asked once each. The union can never exceed
the sum of the per-claim plans, so sharing cannot cost more searches than not
sharing. Each claim's own cap still comes from `budget_manager.get_policy()` as
the single source of truth, because the plan asks *its* claims' questions. The
plan stores both `queries` (planned) and `queries_asked` (actually sent), so a
truncated plan is visible rather than mistaken for a claim with fewer
questions.

### Pre-filter rule and fallback

Before the model is asked anything, a (claim, evidence) pair is skipped when
the document shares no term with the claim's subject or value. Terms are the
first six characters of words of at least four characters from subject +
object, compared against lowered content. The filter may only ever *skip*; it
never adds evidence. A claim with no usable term is examined in full - the
filter is not smart enough to guess, so guessing would skip pages it cannot
judge. Every skip is counted (`pairs_skipped`), traced as an `evidence.prefiltered`
event with a `prefilter` decision rule, and reported beside `pairs_examined`,
because a cheaper run that hides what it never looked at is the failure this
repository exists to avoid.

### No early sufficiency stop

Stopping on evidence sufficiency is deliberately **not** implemented. With
search-all-then-settle a sufficiency stop is unreachable, and an early stop can
hide a later dated change - which is the product's headline outcome. Dedupe,
pre-filter and disclosure are the cost controls; sufficiency is not.

### What is recorded

`tev.research_plans` holds one row per group: `group_key`, `claim_ids`, `queries`
(planned), `queries_asked`, `budget_mode`, `queries_dropped`, `pairs_examined`,
`pairs_skipped`. `queries_dropped` is non-zero exactly when the budget mode
truncated the plan, and the run emits `research.budget_exhausted` so a
truncation is never silent. The report prints examined / skipped per group and
the planned / asked split.


## D-028 - Freshness half-life per volatility class (2026-11-11)

`HALF_LIFE_HOURS` in `app/verify/schemas.py` sets the decay clock for
`freshness_index`: how long a claim's evidence stays "fresh" before a recheck is
due. The previous values (stable 180 d, low 30 d, moderate 7 d, high 24 h,
ephemeral 6 h) let an ephemeral claim decay in under a day, which contradicted
the class name, and let stable claims stay fresh for half a year.

WP-5b sets them to stable 30 d, low 7 d, moderate 24 h, high 6 h, ephemeral 15
min (`0.25` h). `MIN_RECHECK` (1 h) is unchanged, so ephemeral claims are still
never scheduled more often than once an hour even though half a half-life is 7.5
min. Freshness is not a verdict and never changes `claims.status`; it only feeds
the report's next-check disclosure and any future reverify scheduler.

`app/verify/freshness.py` is unchanged; only the table in `schemas.py` moved.
`test_freshness.py` pins the new values: HIGH halves at 6 h, MODERATE at 24 h,
EPHEMERAL at 15 min clamped to the 1 h floor.


## D-029 - A trace is one run, exported as a tree (2026-11-12)

WP-10 exported spans; it did not export a trace. Every span was started with
`INVALID_SPAN` as its parent (40 spans → 40 single-span traces), each run called
`otel_trace.set_tracer_provider()` (a process-wide singleton, so concurrent runs
replaced each other's exporter), and the incoming W3C context was built and
discarded, so no Bifrost or Tavily request ever carried a `traceparent`.

The contract now:

- one run is one OTLP trace, exported at flush with the parent links recorded in
  `trace_spans`;
- ids are derived from the run (`trace_id` from the run UUID or the caller's
  `traceparent`, span ids from span UUIDs), so a span can be matched to its row
  and the exported trace id equals `execution_traces.trace_id`;
- the global tracer provider is never set; each export owns its provider;
- the run context is attached around `execute()` and detached in `finally`, so
  outgoing calls carry the trace even when the caller sent none.

This is observability, not verification. `OTEL_ENABLED=false` produces no spans,
an unavailable collector is a counted export failure, and no code path here can
reach a verdict, a budget or `claims.status`. `RULE_VERSION` is unchanged.
Evidence: `docs/WP10-VALIDATION.md`, `test_otel_validation.py`.
