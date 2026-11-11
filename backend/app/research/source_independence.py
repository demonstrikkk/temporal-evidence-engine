"""Independent corroboration, not a raw source count.

Five articles that copied the same press release are one origin, not five
confirmations. This module fingerprints the *stored revision text* behind each
piece of evidence and groups identical content, so the UI can say "three
sources, two independent origins" instead of implying corroboration that does
not exist.

It never changes a verdict. Settlement and the decision table keep their own
rules; independence is reported next to the evidence so a reader can judge the
strength of agreement.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from app.verify.store import VerifyStore

#: Boilerplate that syndicated copies keep verbatim. Their presence is used only
#: to label a group as press-release-like, never to discard evidence.
_BOILERPLATE = (
    "for immediate release",
    "about us",
    "media contact",
    "forward-looking statements",
    "press release",
    "company description",
)


@dataclass(frozen=True)
class SourceGroup:
    origin_revision_id: str
    revision_ids: tuple[str, ...]
    canonical_url: str
    domain: str
    syndicated: bool
    same_press_release: bool


def _normalise(content: str) -> str:
    text = re.sub(r"\s+", " ", content or "").strip().lower()
    for phrase in _BOILERPLATE:
        text = text.replace(phrase, " ")
    return re.sub(r"\s+", " ", text).strip()


def fingerprint_content(content: str) -> str:
    """Content address of the readable text, with boilerplate removed."""
    return hashlib.sha256(_normalise(content).encode("utf-8", "surrogatepass")
                          ).hexdigest()[:32]


def _is_press_release(content: str) -> bool:
    lowered = (content or "").lower()
    return sum(1 for phrase in _BOILERPLATE if phrase in lowered) >= 2


def assess_independence(store: VerifyStore,
                        evidence: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Group evidence by the content fingerprint of its stored revision."""
    groups: dict[str, dict[str, Any]] = {}
    seen_revisions: set[str] = set()

    for view in evidence:
        revision_id = str(view.get("source_revision_id") or "")
        if not revision_id or revision_id in seen_revisions:
            continue
        seen_revisions.add(revision_id)
        revision = store.get_revision(revision_id) or {}
        content = str(revision.get("content") or "")
        fingerprint = fingerprint_content(content) if content else f"rev:{revision_id}"
        group = groups.setdefault(fingerprint, {
            "origin_revision_id": revision_id,
            "revision_ids": [],
            "canonical_url": str(view.get("source_url") or ""),
            "domain": str(view.get("publisher") or ""),
            "same_press_release": _is_press_release(content),
        })
        group["revision_ids"].append(revision_id)

    result_groups: list[dict[str, Any]] = []
    effective = 0.0
    syndicated_groups = 0
    for group in groups.values():
        syndicated = len(group["revision_ids"]) > 1
        syndicated_groups += int(syndicated)
        # One origin counts fully; each additional copy adds little, because it
        # is the same text, not an independent observation.
        effective += 1.0 + 0.2 * (len(group["revision_ids"]) - 1)
        result_groups.append({
            "origin_revision_id": group["origin_revision_id"],
            "revision_ids": sorted(group["revision_ids"]),
            "canonical_url": group["canonical_url"],
            "domain": group["domain"],
            "syndicated": syndicated,
            "same_press_release": group["same_press_release"],
            "copies": len(group["revision_ids"]),
        })

    result_groups.sort(key=lambda g: g["copies"], reverse=True)
    return {
        "source_count": len(seen_revisions),
        "independent_origins": len(groups),
        "syndicated_groups": syndicated_groups,
        "effective_corroboration": round(effective, 2),
        "groups": result_groups,
        "note": (
            "Syndicated copies are grouped by identical stored text; they do "
            "not add independent confirmation."
            if syndicated_groups else
            "Every source has distinct stored text."
        ),
    }
