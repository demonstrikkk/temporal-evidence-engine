"""Answer -> atomic claims. The model proposes; this module enforces shape.

Claim extraction is the one place where the model is allowed to be creative, so
it is also the place with the most post-processing. Four rules survive every
refactor:

1. **A claim must exist in the answer text.** The model's `claim_text` is
   located in the submitted answer; a claim that cannot be located is dropped,
   not stored with a fabricated span.
2. **Opinions and predictions are not claims.** They are not factual claims that
   failed verification; they are not factual claims at all.
3. **A value is normalised once, here.** Downstream code compares typed values.
4. **No claim is invented to fill a quota.** Fewer claims is a correct outcome.
"""
from __future__ import annotations

import re
from typing import Any

from app.bifrost.client import BifrostClient
from app.core.config import settings
from app.core.logging import log
from app.schemas.evidence import locate_quote
from app.verify.schemas import (ClaimDraft, ClaimExtraction, ClaimType,
                                Volatility, NON_VERIFIABLE_TYPES)
from app.verify.store import iso
from app.verify.value_normalizer import normalize_value

_SYSTEM = """You extract atomic factual claims from an AI-generated answer.

Rules you must follow:
- One claim per sentence or coordinate clause. Split conjunctions.
- A claim must be stated in the answer. Do not add outside knowledge.
- "claim_text" must be an exact substring of the answer where possible.
- "subject" is the entity the claim is about.
- "predicate" is the property or relation being asserted.
- "object_raw" is the asserted value exactly as written: a number, currency,
  version, date, name, or short phrase. Leave it empty only for pure relations.
- "claim_type" is one of: factual, time_sensitive, stable_factual, opinion,
  prediction, ambiguous.
- "time_sensitive" is true when the fact can change without the subject changing:
  a price, a plan limit, an API, library or model version, a deprecation date, a
  law or regulatory threshold, a leader or headcount, a ranking, or a schedule.
- "volatility_class" is one of: ephemeral, high, moderate, low, stable.
  Prices, availability, plan limits and rankings are high or ephemeral; versions,
  laws and leadership are moderate or high; definitions and history are stable.
- "extraction_confidence" is 0..1.

The response must be a json object. Return JSON only: {"claims": [{...}]}
Never return prose, never return a verdict, never judge the claim.
"""


#: The user message. A module constant rather than an f-string inside `_prompt`
#: so `prompts.fingerprint` can hash the instruction a version names; the
#: interpolated answer is data, and data is not part of a prompt's identity.
_TEMPLATE = (
    "ORIGINAL USER QUESTION (context only):\n{original_prompt}\n\n"
    "ANSWER TO EXTRACT FROM:\n{answer}\n\n"
    "Return at most {max_claims} claims. Prefer the claims a reader would check "
    "against a live source."
)


def _prompt(answer: str, original_prompt: str) -> str:
    return _TEMPLATE.format(
        original_prompt=original_prompt or "(not provided)",
        answer=answer,
        max_claims=settings.CLAIM_MAX_PER_ANSWER,
    )


def _guess_value(claim_text: str) -> str:
    """The first checkable value in a claim, when the model did not name one.

    Deliberately regex-based: it runs after the model, not instead of it, and a
    wrong guess is caught by `evidence_speaks_to`, which refuses a quote that
    does not mention the value.
    """
    patterns = [
        r"(?:US\$|\$|€|£|₹)\s?\d[\d,]*(?:\.\d+)?(?:\s*(?:/|per)\s*\w+)?",
        r"\d[\d,]*(?:\.\d+)?\s?%",
        r"\bv?\d+\.\d+(?:\.\d+)*\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b(?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+\d{1,2},?\s+\d{4}\b",
        r"\b\d[\d,]*(?:\.\d+)?\s?(?:GB|MB|TB|ms|seconds?|minutes?|hours?|days?)\b",
        r"\b\d[\d,]*(?:\.\d+)?\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, claim_text or "", re.I)
        if match:
            return match.group(0).strip()
    return ""


def _span(claim_text: str, answer: str) -> tuple[int, int] | None:
    start, end = locate_quote(claim_text, answer)
    if start is None or end is None:
        return None
    return int(start), int(end)


def _clean_claim(raw: dict[str, Any],
                 answer: str) -> tuple[ClaimDraft | None, str]:
    """Return `(draft, "")` or `(None, reason)` — never a silent drop.

    The reason is a machine-readable code, not prose, so the run can count the
    drop paths: a model that paraphrases every claim (`not_locatable`) is a
    different failure from a model that returns opinions (`non_verifiable`).
    """
    text = re.sub(r"\s+", " ", str(raw.get("claim_text") or "")).strip()
    if len(text) < 8:
        return None, "claim_too_short"
    if len(text) > 1200:
        return None, "claim_too_long"
    span = _span(text, answer)
    if span is None:
        # The model paraphrased. Try the sentence containing the paraphrase.
        log.info("claim text was not a substring of the answer; dropped",
                 extra={"data": {"claim": text[:120]}})
        return None, "not_locatable"

    try:
        claim_type = ClaimType(str(raw.get("claim_type") or "factual"))
    except ValueError:
        claim_type = ClaimType.FACTUAL
    if claim_type in NON_VERIFIABLE_TYPES:
        return None, f"non_verifiable:{claim_type.value}"

    object_raw = str(raw.get("object_raw") or "").strip()[:400]
    if not object_raw:
        object_raw = _guess_value(text)
    if not object_raw:
        # A pure relation can still be checked, but only against its own words.
        object_raw = text

    qualifiers = raw.get("qualifiers")
    if not isinstance(qualifiers, dict):
        qualifiers = {}
    temporal_scope = raw.get("temporal_scope")
    if not isinstance(temporal_scope, dict):
        temporal_scope = {}

    try:
        confidence = float(raw.get("extraction_confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    return ClaimDraft(
        claim_text=text,
        subject=str(raw.get("subject") or "")[:200],
        predicate=str(raw.get("predicate") or "")[:200],
        object_raw=object_raw,
        object_normalized=normalize_value(object_raw),
        qualifiers=qualifiers,
        claim_type=claim_type,
        time_sensitive=bool(raw.get("time_sensitive"))
        or claim_type == ClaimType.TIME_SENSITIVE,
        volatility_class=Volatility.parse(raw.get("volatility_class")),
        temporal_scope=temporal_scope,
        extraction_confidence=max(0.0, min(1.0, confidence)),
        span_start=span[0],
        span_end=span[1],
    ), ""


def _overlaps_any(span: tuple[int, int],
                  ranges: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < range_end and end > range_start
               for range_start, range_end in ranges)


def postprocess(payload: Any, answer: str, *,
                skip_non_factual: bool = True,
                span_ranges: list[tuple[int, int]] | None = None,
                drops: list[dict[str, Any]] | None = None) -> list[ClaimDraft]:
    """Pure, testable half of extraction: JSON in, located claims out.

    `span_ranges` restricts the result to proposals that land inside the given
    answer offsets; the coverage repair uses it so a focused re-extraction
    cannot smuggle in claims about the whole answer.

    `drops` is an out-parameter. Every proposal that does not become a claim is
    appended with a reason and its index, because "the extractor returned four
    and we stored two" is only auditable if the other two are named.
    """
    if isinstance(payload, dict):
        raw_claims = payload.get("claims")
    elif isinstance(payload, list):
        raw_claims = payload
    else:
        raw_claims = None
    if not isinstance(raw_claims, list):
        raise ValueError("extraction returned no `claims` array")

    claims: list[ClaimDraft] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_claims):
        if not isinstance(raw, dict):
            _drop(drops, index, raw, "not_an_object")
            continue
        draft, reason = _clean_claim(raw, answer)
        if draft is None:
            _drop(drops, index, raw, reason)
            continue
        if skip_non_factual and draft.claim_type in NON_VERIFIABLE_TYPES:
            _drop(drops, index, raw,
                  f"non_verifiable:{draft.claim_type.value}")
            continue
        if span_ranges is not None and not _overlaps_any(
                (draft.span_start, draft.span_end), span_ranges):
            _drop(drops, index, raw, "outside_requested_span")
            continue
        key = re.sub(r"\W+", " ", draft.claim_text.casefold()).strip()
        if key in seen:
            _drop(drops, index, raw, "duplicate")
            continue
        seen.add(key)
        claims.append(draft)
        if len(claims) >= settings.CLAIM_MAX_PER_ANSWER:
            for rest in range(index + 1, len(raw_claims)):
                _drop(drops, rest, raw_claims[rest], "claim_limit")
            break
    return claims


def _drop(drops: list[dict[str, Any]] | None, index: int,
          raw: Any, reason: str) -> None:
    if drops is None:
        return
    text = ""
    if isinstance(raw, dict):
        text = str(raw.get("claim_text") or "")[:300]
    drops.append({"index": index, "reason": reason, "claim_text": text})



def _receipt(result: Any) -> dict[str, Any]:
    return {
        "provider": result.provider, "model": result.model,
        "request_id": result.request_id, "status": result.status,
        # A replay says *what* it replayed; the receipt says when the answer was
        # originally obtained, so the UI can show the date, not just the word
        # "snapshot". ISO text, not a datetime: the two stores must record the
        # same shape, and only one of them has a JSON encoder to fall back on.
        "created_at": iso(result.created_at),
        "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
        "cost_usd": result.cost_usd, "latency_ms": result.latency_ms,
        "fallback_attempts": result.fallback_attempts,
        # Where the price came from: a bill, a published catalogue, or nowhere.
        # A trace that adds `gateway` and `catalogue` costs into one number makes
        # an estimate look like an invoice.
        "cost_source": getattr(result, "cost_source", "gateway"),
    }


def extract_claims(client: BifrostClient, *, answer: str, original_prompt: str = "",
                   skip_non_factual: bool = True,
                   drops: list[dict[str, Any]] | None = None
                   ) -> tuple[list[ClaimDraft], dict[str, Any]]:
    """One model call. Returns claims plus the call receipt for the UI."""
    result = client.complete(_prompt(answer, original_prompt),
                             stage="extract_claims", system=_SYSTEM,
                             max_tokens=2600)
    receipt = _receipt(result)
    claims = postprocess(result.json(), answer,
                         skip_non_factual=skip_non_factual, drops=drops)
    return claims, receipt


_REPAIR_SYSTEM = """You extract atomic factual claims from specific sentences of
an AI-generated answer that an earlier pass did not cover.

Rules you must follow:
- Return only claims that are stated in the supplied EXCERPTS.
- "claim_text" must be an exact substring of the answer. Copy it verbatim.
- One claim per assertion: split conjunctions and lists of values.
- Do not return opinions, predictions, or recommendations. Do not add outside
  knowledge. Do not repeat a claim you were not shown.
- "object_raw" is the asserted value exactly as written, and it must appear in
  the excerpt.
- "claim_type" is one of: factual, time_sensitive, stable_factual, opinion,
  prediction, ambiguous.
- "volatility_class" is one of: ephemeral, high, moderate, low, stable.
  A price, plan limit, version, law, leader or ranking is volatile; a definition
  or a historical date is stable.

The response must be a json object. Return JSON only: {"claims": [{...}]}
Never return prose, never return a verdict, never judge the claim.
"""


_REPAIR_TEMPLATE = (
    "ANSWER (for locating exact substrings; only the excerpts below are in "
    "scope):\n{answer}\n\n"
    "EXCERPTS THAT NO CLAIM COVERED:\n{excerpts}\n\n"
    "Return at most one claim per uncovered value. Values the audit did not "
    "see a claim for: {values}"
)


def extract_missing_claims(client: BifrostClient, *, answer: str,
                           targets: list[dict[str, Any]],
                           drops: list[dict[str, Any]] | None = None
                           ) -> tuple[list[ClaimDraft], list[str], dict[str, Any]]:
    """One focused re-extraction over the sentences coverage could not attribute.

    `targets` are the audit's repair targets: absolute `(start, end)` windows in
    the answer plus the candidate values that were not covered. At most one call
    is made per run and its results are restricted back to those windows, so the
    repair cannot silently expand into a second full extraction.
    """
    excerpts: list[str] = []
    for index, target in enumerate(targets):
        start, end = int(target["start"]), int(target["end"])
        excerpts.append(f"[{index}] {answer[start:end].strip()}")
    prompt = _REPAIR_TEMPLATE.format(
        answer=answer,
        excerpts="\n\n".join(excerpts),
        values="; ".join(value for target in targets
                         for value in target.get("candidates") or []),
    )
    result = client.complete(prompt, stage="repair_coverage",
                             system=_REPAIR_SYSTEM, max_tokens=1800)
    receipt = _receipt(result)
    ranges = [(int(t["start"]), int(t["end"])) for t in targets]
    claims = postprocess(result.json(), answer, span_ranges=ranges,
                         drops=drops)
    texts = [claim.claim_text for claim in claims]
    return claims, texts, receipt

