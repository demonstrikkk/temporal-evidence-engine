# Product contract - Afterfact

**Display name:** Afterfact · **descriptor:** claim-level verification · **rule set:**
`tev-1.2.0`. The display name is branding only; it never moves `RULE_VERSION`, which
versions the decision table.

**One paragraph.** Paste an AI-generated answer and when it was generated. The
system splits it into atomic claims, researches each one live with Tavily,
stores exact source text, extracts the *event date* separately from the
publication date, and a deterministic engine decides **supported**, **changed**,
**contradicted** or **unverifiable** — with a reason code and a quote a reader
can locate.

## Six capabilities (nothing else is the product)

| Capability | User outcome |
|---|---|
| Answer intake | Submit answer + original prompt + generation time (or explicitly unknown) |
| Claim intelligence | Atomic claims with subject, predicate, value, qualifiers, time sensitivity, volatility |
| Live research | Which sources were searched, extracted, and attached to which claim |
| Temporal verification | Supported at generation time / changed after / contradicted / unverifiable |
| Freshness | How old the last verification of each claim is, decayed by its volatility class, and when a recheck is *recommended* |
| Evidence & history | Exact quotes, conflicting sources, validity intervals, previous checks, reproducible report |

## The signature screen

Three panes: the original answer with claim spans highlighted, the claim
register, and the evidence inspector. Clicking a claim anywhere opens the same
inspector: quote, the four timestamps (answer generated, published, event,
retrieved), settlement result, freshness score, and the exact rule that produced
the verdict.

## Two ways in

Intake is the front door, and there are two of them. Both end in the same run —
same extraction, same research, same verdicts.

- **Paste** (`/check`) — an answer you already have, the time it was generated,
  and optionally the model that wrote it. The model is recorded so its verdicts
  can be compared on the measurement page; left blank, the answer is reported
  there as a pasted answer with no model recorded. The default.
- **Ask** (`/playground`) — ask a time-sensitive question, read the tool calls
  the model made, then scan the answer. Two sources: **this deployment's
  gateway** (the default, so there is no key to paste — the provider key stays on
  the server and the receipt reports who actually answered) or **any
  OpenAI-compatible endpoint you bring** (hosted, or a model running on this
  machine), which is the case the playground was built for. The generation time
  is the model's own, so this is the one intake where that timestamp is exact
  rather than remembered.

The playground writes nothing: no run, no answer, no model call row, and it
produces no verdict. Scanning is a separate `POST /api/verify/runs` — the same
request the paste form makes.

The default ask goes through Bifrost like every other model call. The
bring-your-own-endpoint path is the single documented exception to "every model
call goes through Bifrost" (D-034), because that endpoint and key belong to the
user rather than to this deployment — and it is opt-in, never reached by
omission.

## The comparison page

`/compare` is a view, not a seventh capability. It answers one question a reader
will ask — *which model gave the better answer?* — without inventing the answer.

Columns are measurements: recorded calls, tokens, cost per call, latency, failure
rate, and the verdict mix of the answers a model wrote. The call ledger supplies
the first five; claims supply the last, joined through the model recorded on the
answer that produced them. Rows are grouped by the model that **answered**, not
the one requested, so a gateway fallback or a failed call is counted against the
model that actually ran or failed.

Three rules the page enforces rather than explains:

- **A verdict mix is not a quality score.** A model that writes safe trivialities
  scores high on supported share. The share is shown beside the claim count,
  never on its own, and no ranking is derived from it.
- **An unknown measurement is not zero.** A model with no recorded calls shows an
  em dash, and calls whose price was unknown are excluded from the average cost
  and counted separately. A priced call that genuinely cost nothing still shows
  `$0.00` — that is a measurement.
- **Attribution is earned.** Only an answer whose scan recorded its model
  contributes to that model's verdicts. The extractor that read an answer is not
  its author, so it is never credited. Answers with no recorded model are shown
  as a `pasted answers` group with their own verdict mix — never assigned to
  whichever model is most common, and never given a model id they do not have.

Catalogue rows (the free-model registry) appear for context with their
measurements left empty, and sort below every measured row.

## What "changed" means

The claim was true for the answer's time, and a later, locatable, settled source
establishes a replacement value with an event date **after** the answer was
generated. Anything less is not `changed`; it is `supported`, `contradicted` or
`unverifiable`.

## What this product will not do

- It will not call a fact "false" because it could not find support for it.
- It will not use a publication date as an event date.
- It will not ask a model for the final verdict.
- It will not show a cached result as a live check.
- It will not display a truth percentage it has not calibrated.
- It will not present freshness — the age of the last verification — as a
  probability that the claim is true.
- It will not rank models by a score it cannot measure, or print a zero where the
  truth is "not measured".

## Anti-scope (do not build, even if it looks impressive)

Vector search, background scheduler, agent framework, billing, multi-tenant
auth, browser extension, a chat interface as the product (a conversation with no
verdict at the end — the playground asks one question in order to produce an
answer that gets scanned, and keeps no history), automatic rewriting before
verification, custom Bifrost plugin.
