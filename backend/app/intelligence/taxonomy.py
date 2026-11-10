"""Fixed claim taxonomy and volatility classification.

Categorizes claims into domain taxonomy and volatility classes to determine
TTL, search depth, and evidence thresholds without arbitrary LLM labels.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


TAXONOMY_CATEGORIES = (
    "pricing",
    "availability",
    "leadership",
    "company_metric",
    "product_capability",
    "software_version",
    "api_limit",
    "regulatory",
    "historical_event",
    "scientific_claim",
    "statistical_claim",
    "other",
)


@dataclass(frozen=True)
class TaxonomyProfile:
    category: str
    default_volatility: str
    preferred_search_depth: str
    max_search_budget: int
    max_extract_budget: int


PROFILES: dict[str, TaxonomyProfile] = {
    "pricing": TaxonomyProfile("pricing", "fast", "basic", 2, 3),
    "availability": TaxonomyProfile("availability", "rapid", "basic", 1, 2),
    "leadership": TaxonomyProfile("leadership", "moderate", "basic", 1, 2),
    "company_metric": TaxonomyProfile("company_metric", "moderate", "basic", 2, 3),
    "product_capability": TaxonomyProfile("product_capability", "slow", "basic", 1, 2),
    "software_version": TaxonomyProfile("software_version", "moderate", "basic", 1, 2),
    "api_limit": TaxonomyProfile("api_limit", "moderate", "basic", 2, 3),
    "regulatory": TaxonomyProfile("regulatory", "slow", "basic", 1, 2),
    "historical_event": TaxonomyProfile("historical_event", "static", "basic", 1, 1),
    "scientific_claim": TaxonomyProfile("scientific_claim", "static", "basic", 1, 2),
    "statistical_claim": TaxonomyProfile("statistical_claim", "moderate", "basic", 1, 2),
    "other": TaxonomyProfile("other", "moderate", "basic", 1, 2),
}


_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("pricing", re.compile(r"\b(price|pricing|cost|costs|plan|tier|fee|subscription|\$|usd|eur|monthly|per month)\b", re.I)),
    ("api_limit", re.compile(r"\b(rate limit|quota|rpm|tpm|qps|requests per minute|tokens per minute|context window)\b", re.I)),
    ("software_version", re.compile(r"\b(version|release|released|v\d+\.\d+|changelog|deprecated|deprecation)\b", re.I)),
    ("leadership", re.compile(r"\b(ceo|cto|cfo|chief executive|founder|co-founder|president|board member|director)\b", re.I)),
    ("availability", re.compile(r"\b(available in|out of stock|discontinued|shut down|status|uptime|downtime)\b", re.I)),
    ("company_metric", re.compile(r"\b(headcount|employees|valuation|revenue|profit|funding|users|active users)\b", re.I)),
    ("product_capability", re.compile(r"\b(supports|feature|capability|integrates with|built with|modalities)\b", re.I)),
    ("historical_event", re.compile(r"\b(founded in|acquired|born in|established in|launched in 20\d\d|launched in 19\d\d)\b", re.I)),
    ("statistical_claim", re.compile(r"\b(\d+ percent|\d+%|increase of \d+|decrease of \d+|share of)\b", re.I)),
]


def classify_taxonomy(claim_text: str, predicate: str = "") -> TaxonomyProfile:
    """Classify text and predicate into a deterministic taxonomy category."""
    combined = f"{claim_text} {predicate}".strip().lower()

    for category, pattern in _PATTERNS:
        if pattern.search(combined):
            return PROFILES[category]

    return PROFILES["other"]
