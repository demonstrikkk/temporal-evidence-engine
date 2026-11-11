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

from app.governance.resource_budget import budget_manager
from app.verify.schemas import ClaimDraft

_LEADING = re.compile(r"^(?:the|a|an|in|on|as|at|it|this|that)\s+", re.I)


def _tidy(text: str, limit: int = 220) -> str:
    value = re.sub(r"\s+", " ", (text or "")).strip(" .;:,")
    value = _LEADING.sub("", value)
    return value[:limit].strip()


def plan_queries(claim: ClaimDraft, budget_mode: str = "balanced") -> list[str]:
    queries, _dropped = plan_queries_detail(claim, budget_mode)
    return queries


def plan_queries_detail(claim: ClaimDraft,
                        budget_mode: str = "balanced") -> tuple[list[str], int]:
    """The queries this claim would have, and how many the budget refused.

    The second number exists because a truncated plan that looks complete is a
    lie the report cannot detect later: `plan_queries` caps silently, so a run
    stopped by `conserve` was indistinguishable from a run whose claim simply
    suggested fewer questions. The caller emits the count when it is non-zero.
    """
    # The policy is the single source of truth for how much research a mode may
    # buy. Duplicating the numbers here is how "conserve" once searched as much
    # as "balanced" while the settings panel claimed otherwise.
    policy = budget_manager.get_policy(budget_mode)  # type: ignore[arg-type]
    max_queries = int(policy.get("max_queries_per_claim") or 0)
    if max_queries <= 0 or not policy.get("allow_live_search", True):
        candidates = _candidates(claim)
        return [], len(candidates)

    candidates = _candidates(claim)
    out = candidates[:max_queries]
    return out, len(candidates) - len(out)


def _candidates(claim: ClaimDraft) -> list[str]:
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

    # Domain-specific templates from intelligence layer
    if subject:
        try:
            from app.intelligence.query_templates import generate_deterministic_queries
            templates = generate_deterministic_queries(subject, predicate, value, text, getattr(claim, "qualifiers", None))
            for t in templates:
                if t not in candidates:
                    candidates.append(t)
        except Exception:
            pass

    seen: set[str] = set()
    unique: list[str] = []
    for query in candidates:
        key = query.casefold()
        if len(query) < 6 or key in seen:
            continue
        seen.add(key)
        unique.append(query)
    return unique
