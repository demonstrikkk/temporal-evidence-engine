"""Source quality and authority metadata.

Tracks source reputation signals independent of claim verification:
domain authority signals, HTTPS, first-party vs third-party, crawl freshness,
and whether the source is an official entity domain (from the entity registry).

These signals weight how much trust to place in a source when claims conflict,
without affecting the deterministic settlement gate (which only checks quote
location and statement validity).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from app.core.logging import log
from app.intelligence.entities import entity_registry
from app.verify.store import VerifyStore, iso, now


@dataclass(frozen=True)
class SourceQuality:
    source_id: str
    canonical_url: str
    domain: str
    is_https: bool
    is_official_domain: bool
    first_party: bool  # domain matches claim subject
    crawl_freshness_days: int | None  # days since retrieval
    authority_score: float  # 0.0 to 1.0
    signals: list[str] = field(default_factory=list)


class SourceQualityScorer:
    """Computes authority and quality scores for sources.

    Signals are additive and transparent:
    - HTTPS: +0.1
    - Official entity domain: +0.3
    - First-party (domain matches claim subject): +0.2
    - Recent crawl (< 7 days): +0.2
    - Recent crawl (< 30 days): +0.1
    - Has title: +0.05
    - Has publication date: +0.05
    """

    def __init__(self, store: VerifyStore) -> None:
        self.store = store

    def score_source(
        self,
        source_id: str,
        canonical_url: str,
        domain: str,
        title: str = "",
        published_date: date | None = None,
        retrieved_at: datetime | None = None,
        claim_subject: str = "",
    ) -> SourceQuality:
        signals: list[str] = []
        score = 0.0

        # HTTPS
        is_https = canonical_url.lower().startswith("https://")
        if is_https:
            score += 0.1
            signals.append("https")

        # Official domain (from entity registry)
        official_domains: list[str] = []
        if claim_subject:
            official_domains = entity_registry.official_domains(claim_subject)
        is_official = bool(
            official_domains and any(
                d in domain for d in official_domains))
        if is_official:
            score += 0.3
            signals.append("official_domain")

        # First-party: domain matches claim subject name
        subject_name = re.sub(r"[^a-z0-9]", "", (claim_subject or "").lower())
        domain_name = re.sub(r"[^a-z0-9]", "", domain.lower())
        is_first_party = bool(
            subject_name and domain_name and
            (subject_name in domain_name or domain_name in subject_name))
        if is_first_party:
            score += 0.2
            signals.append("first_party")

        # Crawl freshness
        crawl_freshness_days: int | None = None
        if retrieved_at:
            if isinstance(retrieved_at, str):
                retrieved_at = datetime.fromisoformat(
                    retrieved_at.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - retrieved_at
            crawl_freshness_days = delta.days
            if crawl_freshness_days <= 7:
                score += 0.2
                signals.append("recent_crawl_7d")
            elif crawl_freshness_days <= 30:
                score += 0.1
                signals.append("recent_crawl_30d")

        # Has title
        if title:
            score += 0.05
            signals.append("has_title")

        # Has publication date
        if published_date:
            score += 0.05
            signals.append("has_publication_date")

        score = min(1.0, score)

        return SourceQuality(
            source_id=source_id,
            canonical_url=canonical_url,
            domain=domain,
            is_https=is_https,
            is_official_domain=is_official,
            first_party=is_first_party,
            crawl_freshness_days=crawl_freshness_days,
            authority_score=round(score, 2),
            signals=signals,
        )

    def authority_weight(self, quality: SourceQuality) -> str:
        """Return a human-readable authority tier."""
        if quality.authority_score >= 0.7:
            return "high"
        if quality.authority_score >= 0.4:
            return "medium"
        return "low"


_independence_scorers: dict[str, SourceQualityScorer] = {}


def get_quality_scorer(store: VerifyStore) -> SourceQualityScorer:
    key = id(store)
    if key not in _independence_scorers:
        _independence_scorers[key] = SourceQualityScorer(store)
    return _independence_scorers[key]
