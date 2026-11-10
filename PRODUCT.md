# Product contract — Temporal Evidence Engine

**One paragraph.** Paste an AI-generated answer and when it was generated. The
system splits it into atomic claims, researches each one live with Tavily,
stores exact source text, extracts the *event date* separately from the
publication date, and a deterministic engine decides **supported**, **changed**,
**contradicted** or **unverifiable** — with a reason code and a quote a reader
can locate.

## Five capabilities (nothing else is the product)

| Capability | User outcome |
|---|---|
| Answer intake | Submit answer + original prompt + generation time (or explicitly unknown) |
| Claim intelligence | Atomic claims with subject, predicate, value, qualifiers, time sensitivity, volatility |
| Live research | Which sources were searched, extracted, and attached to which claim |
| Temporal verification | Supported at generation time / changed after / contradicted / unverifiable |
| Evidence & history | Exact quotes, conflicting sources, validity intervals, previous checks, reproducible report |

## The signature screen

Three panes: the original answer with claim spans highlighted, the claim
register, and the evidence inspector. Clicking a claim anywhere opens the same
inspector: quote, three dates, settlement result, and the exact rule that
produced the verdict.

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

## Anti-scope (do not build, even if it looks impressive)

Vector search, background scheduler, agent framework, billing, multi-tenant
auth, browser extension, chat interface, automatic rewriting before
verification, custom Bifrost plugin.
