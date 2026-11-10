"""Source independence tracking.

Detects syndicated content across sources so that five articles copying the
same press release are not counted as five independent confirmations.
Store source relationships such as:
  source_id | origin_source_id | syndicated_from | same_press_release | same_primary_document

This affects how corroboration is described: multiple sources that all trace
to the same origin provide weaker confirmation than multiple independent
origins.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.core.logging import log
from app.verify.store import VerifyStore, iso, new_id, now

#: Fingerprint of a press-release-style article: boilerplate sections that are
#: typically identical across syndicated copies.
_BOILERPLATE_PATTERNS = [
    r"about us",
    r"media contact",
    r"press release",
    r"for immediate release",
    r"company description",
    r"forward-looking statements",
]


@dataclass(frozen=True)
class SourceRelationship:
    source_id: str
    origin_source_id: str | None
    syndicated_from: str | None
    same_press_release: bool
    same_primary_document: bool
    confidence: float  # 0.0 to 1.0


class SourceIndependenceTracker:
    """Detects whether sources are independent or syndicated copies of the
    same origin, so corroboration strength is not overstated."""

    def __init__(self, store: VerifyStore) -> None:
        self.store = store

    def _content_fingerprint(self, content: str) -> str:
        """Content-addressed fingerprint: normalize whitespace and strip
        boilerplate, then hash."""
        normalized = re.sub(r"\s+", " ", content or "").strip().lower()
        for pattern in _BOILERPLATE_PATTERNS:
            normalized = re.sub(pattern, " ", normalized, flags=re.I)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]

    def _is_likely_press_release(self, title: str, content: str) -> bool:
        text = f"{title or ''} {content or ''}".lower()
        hits = sum(1 for p in _BOILERPLATE_PATTERNS if re.search(p, text, re.I))
        return hits >= 2

    def analyze_independence(
        self,
        source_id: str,
        canonical_url: str,
        title: str,
        content: str,
        published_date: date | None = None,
    ) -> SourceRelationship:
        """Analyze whether this source is independent of previously stored
        sources, or a syndicated copy of the same origin."""
        fingerprint = self._content_fingerprint(content)

        # Look for existing sources with the same content fingerprint.
        # We scan all stored sources/revisions to find content matches.
        existing_sources = self.store.list_sources(limit=200)
        for existing in existing_sources:
            existing_id = str(existing.get("id"))
            if existing_id == source_id:
                continue
            revisions = self.store.list_revisions_for_source(
                str(existing_id), limit=5)
            for revision in revisions:
                existing_content = str(revision.get("content") or "")
                existing_fingerprint = self._content_fingerprint(
                    existing_content)
                if existing_fingerprint == fingerprint:
                    # Same content: this is a syndicated copy or primary document.
                    existing_title = str(existing.get("title") or "")
                    is_press_release = self._is_likely_press_release(
                        title, content) or self._is_likely_press_release(
                        existing_title, existing_content)
                    return SourceRelationship(
                        source_id=source_id,
                        origin_source_id=existing_id,
                        syndicated_from=existing_id,
                        same_press_release=is_press_release,
                        same_primary_document=not is_press_release,
                        confidence=0.95,
                    )

        # No matching content found: independent source.
        return SourceRelationship(
            source_id=source_id,
            origin_source_id=None,
            syndicated_from=None,
            same_press_release=False,
            same_primary_document=False,
            confidence=1.0,
        )

    def independence_score(self, source_ids: list[str]) -> dict[str, Any]:
        """Given a list of source IDs, compute an independence score.

        Returns the number of independent origins and the effective
        corroboration strength (which is lower when sources are syndicated
        copies of the same origin).
        """
        if not source_ids:
            return {
                "source_count": 0,
                "independent_origins": 0,
                "syndicated_groups": 0,
                "effective_corroboration": 0.0,
                "groups": [],
            }

        # Build groups of syndicated sources.
        groups: list[list[str]] = []
        assigned: set[str] = set()

        for sid in source_ids:
            if sid in assigned:
                continue
            group = [sid]
            assigned.add(sid)
            # Check other unassigned sources for syndication relationship.
            for other in source_ids:
                if other in assigned:
                    continue
                rel = self.analyze_independence(other, "", "", "")
                if rel.syndicated_from == sid or rel.origin_source_id == sid:
                    group.append(other)
                    assigned.add(other)
            groups.append(group)

        independent_origins = len(groups)
        syndicated_groups = sum(1 for g in groups if len(g) > 1)
        # Effective corroboration: each independent origin counts as 1.0,
        # each syndicated copy within a group counts as 0.2 (diminishing).
        effective = 0.0
        for g in groups:
            effective += 1.0 + 0.2 * (len(g) - 1)

        return {
            "source_count": len(source_ids),
            "independent_origins": independent_origins,
            "syndicated_groups": syndicated_groups,
            "effective_corroboration": round(effective, 2),
            "groups": groups,
        }


#: Global instance created lazily per store (store is injected).
_independence_trackers: dict[str, SourceIndependenceTracker] = {}


def get_independence_tracker(store: VerifyStore) -> SourceIndependenceTracker:
    key = id(store)
    if key not in _independence_trackers:
        _independence_trackers[key] = SourceIndependenceTracker(store)
    return _independence_trackers[key]
