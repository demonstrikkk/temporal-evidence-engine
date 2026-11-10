"""Deterministic search query generation based on claim taxonomy and entity registry.

Generates targeted, high-precision search queries without calling an LLM,
reserving model query planning only for genuinely ambiguous propositions.
"""
from __future__ import annotations

import re
from typing import Any

from app.intelligence.entities import entity_registry
from app.intelligence.taxonomy import classify_taxonomy


def generate_deterministic_queries(
    subject: str,
    predicate: str,
    object_raw: str = "",
    claim_text: str = "",
    qualifiers: dict[str, Any] | None = None,
) -> list[str]:
    """Generate 1-2 targeted search queries deterministically."""
    canonical_subject = entity_registry.resolve(subject)
    clean_subj = canonical_subject.strip()
    if not clean_subj:
        return []

    profile = classify_taxonomy(claim_text, predicate)
    cat = profile.category

    queries: list[str] = []

    clean_subj = canonical_subject.strip()
    clean_pred = predicate.strip().replace("_", " ")
    clean_val = object_raw.strip()

    if cat == "pricing":
        queries.append(f"{clean_subj} official pricing")
        if clean_val:
            queries.append(f"{clean_subj} {clean_pred} {clean_val}")
        else:
            queries.append(f"{clean_subj} pricing plans")

    elif cat == "api_limit":
        queries.append(f"{clean_subj} API rate limits documentation")
        if clean_val:
            queries.append(f"{clean_subj} {clean_val} rate limit")

    elif cat == "software_version":
        queries.append(f"{clean_subj} release notes changelog")
        if clean_val:
            queries.append(f"{clean_subj} version {clean_val}")

    elif cat == "leadership":
        queries.append(f"{clean_subj} executive leadership {clean_pred}")
        if clean_val:
            queries.append(f"{clean_subj} {clean_val} {clean_pred}")

    elif cat == "availability":
        queries.append(f"{clean_subj} availability status")
        if clean_val:
            queries.append(f"{clean_subj} {clean_pred} {clean_val}")

    elif cat == "company_metric":
        queries.append(f"{clean_subj} {clean_pred}")
        if clean_val:
            queries.append(f"{clean_subj} {clean_val} {clean_pred}")

    else:
        # General fallback query template
        if clean_val:
            queries.append(f"{clean_subj} {clean_pred} {clean_val}")
        else:
            queries.append(f"{clean_subj} {clean_pred}")

    # Deduplicate and return at most profile budget
    seen = set()
    final_queries = []
    for q in queries:
        norm = q.lower().strip()
        if norm not in seen:
            seen.add(norm)
            final_queries.append(q)

    return final_queries[:profile.max_search_budget]
