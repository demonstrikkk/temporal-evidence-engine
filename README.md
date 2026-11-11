# Temporal Evidence Engine

An AI answer is not true or false. It is a set of claims that may have been
supported when it was written, changed afterwards, been contradicted, or never
been verifiable. This product decides which, per claim, and shows the proof.

Paste an answer and its generation time. You get:

- **atomic claims** with subject, predicate, value, qualifiers and volatility;
- **live research** through Tavily (search + extract), with stored source
  revisions;
- **exact quotes** that resolve against the stored text;
- **four separate timestamps** — when the answer was generated, when the source
  was published, when the fact actually changed, and when we retrieved it;
- a **deterministic verdict** per claim — `supported`, `changed`,
  `contradicted`, `unverifiable` — with a reason code;
- a **reproducible evidence pack** (Markdown/JSON).

Every model call goes through a **Bifrost** gateway and is visible in the UI:
provider, model, tokens, cost, latency, fallback attempts.

## The rule that matters

A claim becomes `changed` only when a replacement value is **locatable**,
**settled** through `validator.settle`, and carries an **event date after the
answer was generated**. A newer article is not a change. A publication date is
not an event date. A failed search is not a contradiction.

How the pieces fit — Tavily as discovery only, Bifrost as the sole model path,
the DataGoblin substrate, the five cache layers and the rule table — is mapped
file by file in [`docs/INTEGRATION.md`](docs/INTEGRATION.md), including what is
configured here but cannot actually run on this machine.

## Stack

- Backend: Python 3.11+, FastAPI, psycopg 3, Postgres (schema `tev`)
- Model gateway: Bifrost (`/v1/chat/completions`, OpenAI-compatible)
- Research: Tavily Search + Extract, guarded crawler fallback
- Frontend: React 18, Vite 5, react-router 7, Tailwind 3

## Quick start

```powershell
# 1. Python env (reuse the seeded one or create it)
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

# 2. Configure
Copy-Item .env.example .env
#    fill BIFROST_* and TAVILY_API_KEY (see "Keys you need")

# 3. Bifrost gateway — a downloaded Go binary, no Docker required
#    (puts the binary in %LOCALAPPDATA%\bifrost and imports bifrost\config.json)
.\scripts\bifrost_up.ps1

# 4. Database (optional locally: PERSISTENCE=local uses a JSON state file)
$env:DATABASE_URL = 'postgresql://...'
backend\.venv\Scripts\python.exe backend\scripts\db_migrate.py

# 5. API + UI
backend\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000   # from backend\
cd frontend; npm ci; npm run dev
```

`scripts\bifrost_up.ps1 -Stop` stops the gateway; `-Reset` wipes its config
store and re-imports `bifrost\config.json`.

Open `http://localhost:5173`, paste an answer, set the generation time, verify.

## Configuration

Everything is in `.env.example`. The important ones:

| Variable | Meaning |
|---|---|
| `BIFROST_BASE_URL` | gateway address, default `http://127.0.0.1:8080` |
| `BIFROST_VIRTUAL_KEY` | the only credential the backend holds for models |
| `BIFROST_MODEL_EXTRACTOR` / `_JUDGE` / `_REWRITER` | model routing per task |
| `TAVILY_API_KEY` | live research |
| `PERSISTENCE` | `postgres` (default when `DATABASE_URL` set) or `local` |
| `CACHE_MODE` | `read-write`, `read-only` (snapshot demo), `off` |

`RULE_VERSION` is not a setting. It is a constant in
`backend/app/core/config.py`, stamped on every verdict and every report, and a
test fails if anyone turns it back into an environment variable. A label that can
be changed without changing a rule is not a rule version.

## Evaluation

```powershell
# hermetic: gold claims + gold evidence, no keys, no network, no database
backend\.venv\Scripts\python.exe eval\generate_cases.py      # rebuild the generated half
backend\.venv\Scripts\python.exe eval\run_eval.py
backend\.venv\Scripts\python.exe eval\run_eval.py --split heldout

# ablations: same model proposals, four different pipelines
backend\.venv\Scripts\python.exe eval\ablate.py

# live: real Bifrost + Tavily against eval\gold_live.jsonl; costs credits
backend\.venv\Scripts\python.exe eval\run_eval.py --live --cases eval\gold_live.jsonl --pause 20
```

Hermetic mode holds extraction and relationship proposals constant so the
measured part is the one that must never be probabilistic: quote location, the
settlement gate, event-date reading, rule precedence and the final status. Its
gate is exact — 100% status accuracy, zero false `supported`, zero false
`changed`, every stored quote resolving at its recorded offsets, every stated
event date correct.

Live mode answers a different question — does the whole thing work against the
real world? — so it gates only on what the product controls: every gold claim
extracted, every run finished, every stored quote valid, and no claim marked
`supported` or `changed` when the gold says otherwise. Live status accuracy is
printed as evidence, not used as a switch, because the web is not deterministic.
Reports include abstention recall, over-abstention and recorded model cost.
See `docs/BUILD-PLAN.md` §14 and `DECISIONS.md` D-014.

Latest recorded runs (2026-11-11):

- hermetic, all cases: 106 cases / 107 claims (84 dev / 22 held out), status
  accuracy 100%, zero false `supported`, zero false `changed`, 138/138 quotes
  valid, event dates 44/44, `$0.0000` recorded model cost, **Gate: PASS**.
- hermetic, held-out quarter only: 22 cases / 22 claims, status accuracy 100%,
  28/28 quotes, event dates 8/8, **Gate: PASS**. The 18 hand-written cases are
  `dev` and are never counted as held out: they shaped the rules.
- ablation on the same 106 cases, with identical model proposals in every arm:
  `full` 100.0%; `evidence-gated` 65.4% (6 false `supported`, 16 false
  `changed`); `baseline` 57.9% (14, 16); `baseline-collapsed` 53.3% (14, 0).
  The baseline gets every `changed` case right, which is why the comparison is
  worth printing: it fails on publication read as event time, on scope, and on
  two first-party pages disagreeing.
- live: 3 cases / 3 claims against Groq + Tavily, status accuracy 100%, 11/11
  quotes valid, zero false `supported`/`changed`, `$0.0000` (free tier),
  **Gate: PASS**.
- replay `a139882f-abe6-494f-bcf9-1219bfea5c75`: same four verdicts, 30
  `model.snapshot` events, **0 gateway calls**, `$0`, and 12/12 quotes resolving
  at their stored offsets.
- replay `c0496df3-eba3-4baa-a4bb-135f5ad96055`: after the receipt fix, all
  **30/30** replays carry the timestamp they were originally obtained, still
  **0 gateway calls**, `$0`, 12/12 quotes resolving.
- cache-off run `1f64a7f8-52de-400a-831d-294dd160a93c`: ended `partial` because
  Groq rate-limited real calls; 8 audit rows (ok and error) and 0 replays were
  still recorded. A failed call is never a verdict.

## Verifying the build

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
cd frontend; npm test; npm run build
backend\.venv\Scripts\python.exe eval\run_eval.py
```

## Keys you need

1. **Bifrost gateway** running locally — `.\scripts\bifrost_up.ps1` starts the
   binary, imports `bifrost\config.json` and checks the key. Two providers:
   a hosted primary (Groq: `GROQ_API_KEY`) and a local Ollama fallback
   (`OLLAMA_BASE_URL`, default `http://127.0.0.1:11434`) with a chat model
   pulled. A fallback that has never been exercised is not a fallback.
2. **Tavily API key** — `tvly-...` from tavily.com, in `.env` as
   `TAVILY_API_KEY`.
3. **The virtual key** the backend uses is `sk-bf-tev-local-dev`, defined in
   `bifrost\config.json` and mirrored in `.env` as `BIFROST_VIRTUAL_KEY`. It is
   not a provider credential and only works against a gateway bound to
   `127.0.0.1`.
4. Provider keys live in the gateway's environment (`GROQ_API_KEY`,
   `OLLAMA_BASE_URL`), never in this backend and never in `.env`.

## Licence

MIT. Contains code derived from DataGoblin; see `NOTICE` and `UPSTREAM.md`.
