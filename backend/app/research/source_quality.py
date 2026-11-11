"""Source authority signals, reported beside the evidence.

These are transparency signals, not a second settlement gate. A quote still
counts only when it resolves against stored text and establishes the asserted
value. Authority explains *who* said it and how fresh the crawl is; it never
promotes or demotes a verdict on its own.

Scores are additive and printed as a list of signals so a reviewer can audit
the number instead of trusting a black box.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from app.intelligence.entities import entity_registry
from app.verify.store import VerifyStore, parse_iso


@dataclass(frozen=True)
class SourceQuality:
    revision_id: str
    canonical_url: str
    domain: str
    is_https: bool
    is_official_domain: bool
    first_party: bool
    crawl_freshness_days: int | None
    authority_score: float
    authority_tier: str
    signals: list[str] = field(default_factory=list)


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = parse_iso(value)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _label(value: str) -> str:
    """First host label with punctuation removed, mirroring `_credibility`."""
    host = (value or "").lower().split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return re.sub(r"[^a-z0-9]", "", host.split(".")[0]) if host else ""


def score_evidence(store: VerifyStore, claim_subject: str,
                   evidence: Iterable[dict[str, Any]]) -> list[SourceQuality]:
    """Score every distinct source revision in an evidence list."""
    subject_key = re.sub(r"[^a-z0-9]", "", (claim_subject or "").lower())
    scored: list[SourceQuality] = []
    seen: set[str] = set()

    for view in evidence:
        revision_id = str(view.get("source_revision_id") or "")
        if not revision_id or revision_id in seen:
            continue
        seen.add(revision_id)
        revision = store.get_revision(revision_id) or {}
        source = store.get_source(str(revision.get("source_id") or "")) or {}
        url = str(view.get("source_url") or source.get("canonical_url") or "")
        domain = str(view.get("publisher") or source.get("domain") or "")
        title = str(view.get("source_title") or source.get("title") or "")
        published = view.get("published_date") or revision.get("published_date")
        retrieved = _coerce_datetime(
            view.get("retrieved_at") or revision.get("retrieved_at"))

        score = 0.0
        signals: list[str] = []

        is_https = url.lower().startswith("https://")
        if is_https:
            score += 0.1
            signals.append("https")

        official = entity_registry.official_domains(claim_subject)
        is_official = bool(official and any(d in domain for d in official))
        if is_official:
            score += 0.3
            signals.append("official_domain")

        label = _label(domain)
        first_party = bool(
            subject_key and label and len(label) >= 3 and
            (subject_key.startswith(label) or label.startswith(subject_key[:12])))
        if first_party:
            score += 0.2
            signals.append("first_party")

        freshness: int | None = None
        if retrieved:
            freshness = max(0, (datetime.now(timezone.utc) - retrieved).days)
            if freshness <= 7:
                score += 0.2
                signals.append("crawled_last_7d")
            elif freshness <= 30:
                score += 0.1
                signals.append("crawled_last_30d")

        if title:
            score += 0.05
            signals.append("has_title")
        if published:
            score += 0.05
            signals.append("has_publication_date")

        score = min(1.0, round(score, 2))
        tier = "high" if score >= 0.7 else "medium" if score >= 0.4 else "low"
        scored.append(SourceQuality(
            revision_id=revision_id,
            canonical_url=url,
            domain=domain,
            is_https=is_https,
            is_official_domain=is_official,
            first_party=first_party,
            crawl_freshness_days=freshness,
            authority_score=score,
            authority_tier=tier,
            signals=signals,
        ))
    return scored


def summarise_quality(scored: Iterable[SourceQuality]) -> dict[str, Any]:
    rows = list(scored)
    counts = {"high": 0, "medium": 0, "low": 0}
    for row in rows:
        counts[row.authority_tier] += 1
    return {
        "scored_sources": len(rows),
        "tiers": counts,
        "official_sources": sum(1 for r in rows if r.is_official_domain),
        "first_party_sources": sum(1 for r in rows if r.first_party),
        "sources": [
            {
                "revision_id": r.revision_id,
                "domain": r.domain,
                "authority_score": r.authority_score,
                "authority_tier": r.authority_tier,
                "signals": r.signals,
                "is_official_domain": r.is_official_domain,
                "first_party": r.first_party,
                "crawl_freshness_days": r.crawl_freshness_days,
            }
            for r in rows
        ],
    }
