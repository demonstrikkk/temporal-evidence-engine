"""Scope-aware claim deduplication.

Identifies semantically equivalent propositions within an answer to avoid
wasting research queries on duplicate assertions, while strictly preserving
differing value scopes (e.g., '$99/month' vs '$99/user/month').
"""
from __future__ import annotations

import re
from typing import Any


def _normalize_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _extract_scope_tokens(qualifiers: dict[str, Any] | None, raw_text: str) -> set[str]:
    """Extract scope indicators such as per-user, annual, enterprise, region."""
    tokens = set()
    if qualifiers:
        for k, v in qualifiers.items():
            tokens.add(f"{_normalize_token(k)}:{_normalize_token(str(v))}")

    lower_text = (raw_text or "").lower()

    # Normalize frequency tokens
    if "per month" in lower_text or "monthly" in lower_text or "/month" in lower_text:
        tokens.add("period:monthly")
    elif "per year" in lower_text or "annually" in lower_text or "annual" in lower_text or "/year" in lower_text:
        tokens.add("period:annually")

    # Normalize per-unit tokens
    if "per user" in lower_text or "/user" in lower_text or "per seat" in lower_text or "/seat" in lower_text:
        tokens.add("unit:per_user")

    for tier in ("starter", "pro", "enterprise", "business", "team", "free"):
        if tier in lower_text:
            tokens.add(f"tier:{tier}")

    return tokens


def deduplicate_propositions(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate claims with identical subject, predicate, and value scope."""
    seen_groups: dict[str, dict[str, Any]] = {}
    deduped: list[dict[str, Any]] = []

    for claim in claims:
        subj = _normalize_token(claim.get("subject") or "")
        pred = _normalize_token(claim.get("predicate") or "")
        raw_obj = _normalize_token(claim.get("object_raw") or "")
        scope = sorted(list(_extract_scope_tokens(claim.get("qualifiers"), claim.get("claim_text") or "")))

        # Key captures subject, predicate, and scope
        group_key = f"{subj}::{pred}::{','.join(scope)}::{raw_obj}"

        if group_key in seen_groups:
            # Merge span info
            existing = seen_groups[group_key]
            existing_spans = existing.setdefault("merged_spans", [])
            existing_spans.append({
                "span_start": claim.get("span_start"),
                "span_end": claim.get("span_end"),
                "claim_text": claim.get("claim_text"),
            })
            continue

        claim_copy = dict(claim)
        claim_copy["merged_spans"] = [{
            "span_start": claim.get("span_start"),
            "span_end": claim.get("span_end"),
            "claim_text": claim.get("claim_text"),
        }]
        seen_groups[group_key] = claim_copy
        deduped.append(claim_copy)

    return deduped
