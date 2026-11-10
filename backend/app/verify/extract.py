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
- "time_sensitive" is true when the fact can change without the subject changing.
- "volatility_class" is one of: ephemeral, high, moderate, low, stable.
  Prices, availability, staffing and rankings are high or ephemeral.
- "extraction_confidence" is 0..1.

Return JSON only: {"claims": [{...}]}
Never return prose, never return a verdict, never judge the claim.
"""


def _prompt(answer: str, original_prompt: str) -> str:
    return (
        f"ORIGINAL USER QUESTION (context only):\n{original_prompt or '(not provided)'}\n\n"
        f"ANSWER TO EXTRACT FROM:\n{answer}\n\n"
        f"Return at most {settings.CLAIM_MAX_PER_ANSWER} claims. Prefer the "
        f"claims a reader would check against a live source."
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


def _clean_claim(raw: dict[str, Any], answer: str) -> ClaimDraft | None:
    text = re.sub(r"\s+", " ", str(raw.get("claim_text") or "")).strip()
    if len(text) < 8 or len(text) > 1200:
        return None
    span = _span(text, answer)
    if span is None:
        # The model paraphrased. Try the sentence containing the paraphrase.
        log.info("claim text was not a substring of the answer; dropped",
                 extra={"data": {"claim": text[:120]}})
        return None

    try:
        claim_type = ClaimType(str(raw.get("claim_type") or "factual"))
    except ValueError:
        claim_type = ClaimType.FACTUAL
    if claim_type in NON_VERIFIABLE_TYPES:
        return None

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
    )


def postprocess(payload: Any, answer: str, *,
                skip_non_factual: bool = True) -> list[ClaimDraft]:
    """Pure, testable half of extraction: JSON in, located claims out."""
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
    for raw in raw_claims:
        if not isinstance(raw, dict):
            continue
        draft = _clean_claim(raw, answer)
        if draft is None:
            continue
        if skip_non_factual and draft.claim_type in NON_VERIFIABLE_TYPES:
            continue
        key = re.sub(r"\W+", " ", draft.claim_text.casefold()).strip()
        if key in seen:
            continue
        seen.add(key)
        claims.append(draft)
        if len(claims) >= settings.CLAIM_MAX_PER_ANSWER:
            break
    return claims


def extract_claims(client: BifrostClient, *, answer: str, original_prompt: str = "",
                   skip_non_factual: bool = True) -> tuple[list[ClaimDraft], dict[str, Any]]:
    """One model call. Returns claims plus the call receipt for the UI."""
    result = client.complete(_prompt(answer, original_prompt),
                             stage="extract_claims", system=_SYSTEM,
                             max_tokens=2600)
    receipt = {
        "provider": result.provider, "model": result.model,
        "request_id": result.request_id, "status": result.status,
        "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
        "cost_usd": result.cost_usd, "latency_ms": result.latency_ms,
        "fallback_attempts": result.fallback_attempts,
    }
    claims = postprocess(result.json(), answer,
                         skip_non_factual=skip_non_factual)
    return claims, receipt
