"""The model proposes a relationship. It is never trusted with the label.

Allowed proposals: `supports`, `contradicts`, `mentions_only`, `silent`. Anything
else is `silent`, because an unrecognised string has not established anything.

The model must return a quote copied from the supplied windows. That quote is
then located in the **full stored text**; if it does not resolve there, the
proposal is discarded. This is the difference between "the model said so" and
"a reader can find it".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.bifrost.client import BifrostClient, BifrostError
from app.core.logging import log
from app.research.sources import render_windows
from app.schemas.evidence import locate_quote
from app.verify.schemas import ClaimDraft, Relationship

_SYSTEM = """You compare one claim with one source document.

Decide what THIS DOCUMENT says about the claim:
- "supports": the document states the same fact (same subject, same value).
- "contradicts": the document states a DIFFERENT value for the same property at
  the same scope (same currency, same period, same unit basis).
- "mentions_only": the document discusses the subject but says nothing about
  this property or value.
- "silent": the document does not address the claim at all.

You must return the exact sentence(s) from the document that justify your
answer, copied character-for-character from the numbered windows. Do not
paraphrase, do not join separate sentences, do not add ellipses.

If the document states a different value, put that value in "candidate_value"
exactly as written. If the document states the same value, "candidate_value"
must be empty. If the document does not state a value, leave it empty.

Return JSON only:
{"relationship": "...", "quote": "...", "candidate_value": "...",
 "reasoning": "one sentence"}

Never judge whether the claim is true in general. Only report what this
document says.
"""


@dataclass(frozen=True)
class Proposal:
    relationship: Relationship
    quote: str = ""
    candidate_value_raw: str = ""
    reasoning: str = ""
    located: bool = False
    start: int | None = None
    end: int | None = None
    error: str = ""
    model_call: dict[str, Any] = field(default_factory=dict)


def _prompt(claim: ClaimDraft, content: str, url: str, title: str) -> str:
    import json
    claim_json = json.dumps({
        "claim": claim.claim_text,
        "subject": claim.subject,
        "predicate": claim.predicate,
        "value": claim.object_raw,
        "claim_type": claim.claim_type.value,
    }, ensure_ascii=False)
    windows = render_windows(content, claim.claim_text, max_chars=6000)
    return (f"CLAIM:\n{claim_json}\n\n"
            f"SOURCE: {title or url}\n{url}\n\n"
            f"DOCUMENT WINDOWS:\n{windows}\n\n"
            f"What does this document say about the claim?")


def _strip_quote(quote: str) -> str:
    text = str(quote or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`":
        text = text[1:-1].strip()
    return text.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')


def locate(quote: str, content: str) -> tuple[int | None, int | None]:
    """Locate a model quote, tolerating only typographic folding.

    Smart quotes and apostrophes are folded because models re-typeset them; the
    stored text keeps the originals. The returned offsets still address the
    stored text.
    """
    start, end = locate_quote(quote, content)
    if start is not None:
        return start, end
    folded = _strip_quote(quote)
    if folded != quote:
        start, end = locate_quote(folded, content)
    return start, end


def propose(client: BifrostClient, *, claim: ClaimDraft, content: str,
            url: str = "", title: str = "", claim_id: str | None = None) -> Proposal:
    if not content.strip():
        return Proposal(Relationship.SILENT, error="the stored source text is empty")
    try:
        result = client.complete(_prompt(claim, content, url, title),
                                 stage="propose_relationship", system=_SYSTEM,
                                 claim_id=claim_id, max_tokens=700)
    except BifrostError as exc:
        log.warning("relationship proposal failed",
                    extra={"data": {"kind": exc.kind, "error": str(exc)[:200]}})
        return Proposal(Relationship.SILENT, error=f"model_failed: {exc}")

    receipt = {"provider": result.provider, "model": result.model,
               "request_id": result.request_id, "status": result.status,
               "created_at": result.created_at,
               "input_tokens": result.input_tokens,
               "output_tokens": result.output_tokens,
               "cost_usd": result.cost_usd, "latency_ms": result.latency_ms,
               "fallback_attempts": result.fallback_attempts}

    try:
        payload = result.json()
    except ValueError:
        return Proposal(Relationship.SILENT, error="model_returned_non_json",
                        model_call=receipt)
    if not isinstance(payload, dict):
        return Proposal(Relationship.SILENT, error="model_returned_non_object",
                        model_call=receipt)

    relationship = Relationship.parse(payload.get("relationship"))
    quote = _strip_quote(str(payload.get("quote") or ""))
    candidate = str(payload.get("candidate_value") or "").strip()[:400]
    reasoning = str(payload.get("reasoning") or "")[:500]

    if relationship == Relationship.SILENT:
        return Proposal(relationship, quote, candidate, reasoning,
                        model_call=receipt)

    if not quote:
        return Proposal(Relationship.MENTIONS_ONLY,
                        reasoning="no quote was returned, so nothing is established",
                        error="missing_quote", model_call=receipt)

    start, end = locate(quote, content)
    if start is None or end is None:
        log.info("proposed quote does not resolve in stored text",
                 extra={"data": {"quote": quote[:120]}})
        return Proposal(Relationship.SILENT, quote, candidate, reasoning,
                        error="quote_not_locatable", model_call=receipt)

    # A contradiction without a replacement value cannot support `changed`. It
    # is still a contradiction for the verdict, but the caller needs to know the
    # value is absent, so it is reported honestly rather than inferred.
    if relationship == Relationship.CONTRADICTS and not candidate:
        candidate = ""

    return Proposal(relationship, content[start:end], candidate, reasoning,
                    located=True, start=int(start), end=int(end),
                    model_call=receipt)
