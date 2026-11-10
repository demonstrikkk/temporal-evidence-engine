"""Turning a claim into search queries is deterministic, and stays deterministic.

A model-planned query looks impressive and makes the gold evaluation
unreproducible: the same claim searched twice produces different evidence, so a
verdict changes for reasons unrelated to the sources. Templates over the claim's
own fields are poorer queries, but they are stable, and the cost of a weaker
query is a missed source — which is reported as `unverifiable`, not as a wrong
verdict.
"""
from __future__ import annotations

import re

from app.core.config import settings
from app.verify.schemas import ClaimDraft

_LEADING = re.compile(r"^(?:the|a|an|in|on|as|at|it|this|that)\s+", re.I)


def _tidy(text: str, limit: int = 220) -> str:
    value = re.sub(r"\s+", " ", (text or "")).strip(" .;:,")
    value = _LEADING.sub("", value)
    return value[:limit].strip()


def plan_queries(claim: ClaimDraft) -> list[str]:
    subject = _tidy(claim.subject or "", 100)
    predicate = _tidy(claim.predicate or "", 100)
    value = _tidy(claim.object_raw or "", 80)
    text = _tidy(claim.claim_text, 220)

    candidates: list[str] = []
    if subject and predicate:
        candidates.append(f"{subject} {predicate}")
    if subject and value and value not in {subject, predicate}:
        candidates.append(f"{subject} {value}")
    if text:
        candidates.append(text)
    if subject and predicate:
        candidates.append(f"{subject} {predicate} official")

    seen: set[str] = set()
    out: list[str] = []
    for query in candidates:
        key = query.casefold()
        if len(query) < 6 or key in seen:
            continue
        seen.add(key)
        out.append(query)
        if len(out) >= settings.RESEARCH_MAX_QUERIES_PER_CLAIM:
            break
    return out
