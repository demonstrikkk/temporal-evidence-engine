"""Sources are stored before anything is quoted out of them.

A quote cites a `source_revisions` row. That row holds the *full* extracted
text, and every offset in `evidence_spans` indexes into that text. Two
consequences are load-bearing:

* Revisions are append-only per `(source_id, content_hash)`. Same bytes reuse
  the row; different bytes get a new row. Updating a revision in place is how
  recorded offsets drift away from the quote they were computed against.
* Excerpt selection for the model returns *windows* `(chunk_start, chunk_end)`
  into the full text. The model may only see a window; the quote it returns is
  still located in the full text, and its offsets are absolute.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.verify.store import VerifyStore, iso, new_id, now

#: Query parameters that identify a campaign, not a document. Removing them is
#: what lets the same article found twice be one source rather than two.
_TRACKING = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "gclid", "fbclid", "mc_cid", "mc_eid", "ref", "ref_src",
    "spm", "igshid", "yclid", "msclkid", "_hsenc", "_hsmi",
}
#: Hosts whose paths are case-sensitive and must not be lower-cased.
_CASE_SENSITIVE_HOSTS = {"wikipedia.org"}


@dataclass(frozen=True)
class StoredRevision:
    source: dict[str, Any]
    revision: dict[str, Any]
    content_hash: str
    reused: bool

    @property
    def content(self) -> str:
        return str(self.revision.get("content") or "")

    @property
    def revision_id(self) -> str:
        return str(self.revision["id"])


def canonicalize_url(url: str) -> str:
    """One URL per document.

    Fragment dropped, host lower-cased, default ports dropped, tracking
    parameters removed, remaining query sorted. Path case is preserved except on
    hosts where it is known to be insignificant.
    """
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw if "://" in raw else f"https://{raw}")
    except ValueError:
        return raw
    scheme = "https" if parts.scheme in {"http", "https", ""} else parts.scheme
    host = (parts.hostname or "").lower()
    if not host:
        return raw
    port = parts.port
    if port in (80, 443):
        port = None
    if port:
        host = f"{host}:{port}"
    path = parts.path or "/"
    if any(host == h or host.endswith("." + h) for h in _CASE_SENSITIVE_HOSTS):
        pass
    else:
        path = path.rstrip("/") or "/"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in _TRACKING and not k.lower().startswith("utm_")]
    return urlunsplit((scheme, host, path, urlencode(sorted(query)), ""))


def domain_of(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", "surrogatepass")).hexdigest()


def _clean_text(content: str) -> str:
    """Remove what breaks offsets, and nothing else.

    NUL bytes and a BOM are dropped because they corrupt JSON/Postgres round
    trips. Whitespace is *not* collapsed: the stored text is the text the
    offsets were computed against, and every reflow is a chance to drift.
    """
    return (content or "").replace("\x00", "").lstrip("\ufeff")


def save_source(store: VerifyStore, url: str, title: str = "") -> dict[str, Any]:
    canonical = canonicalize_url(url)
    source = {"id": new_id(), "canonical_url": canonical,
              "domain": domain_of(canonical), "title": (title or "")[:300],
              "first_seen_at": iso(now())}
    stored = store.add_source(source)
    return stored


def save_revision(store: VerifyStore, *, source: dict[str, Any], content: str,
                  published_date: date | None = None,
                  published_date_source: str = "unknown",
                  extraction_method: str = "tavily_extract",
                  tavily_request_id: str = "",
                  fetch_outcome: dict[str, Any] | None = None,
                  retrieved_at: datetime | None = None) -> StoredRevision:
    text = _clean_text(content)
    digest = content_hash(text)
    existing = store.find_revision(str(source["id"]), digest)
    if existing:
        return StoredRevision(source, existing, digest, True)
    row = {
        "id": new_id(), "source_id": source["id"], "content_hash": digest,
        "content": text,
        "published_date": published_date.isoformat() if published_date else None,
        "published_date_source": published_date_source,
        "retrieved_at": iso(retrieved_at or now()),
        "extraction_method": extraction_method,
        "tavily_request_id": tavily_request_id,
        "fetch_outcome": fetch_outcome or {},
    }
    stored = store.add_revision(row)
    return StoredRevision(source, stored, digest, False)


# ---------------------------------------------------------------------------
# excerpt windows
# ---------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"[.!?]\s")


def _terms(query: str, extra: Iterable[str] = ()) -> list[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-.]+", f"{query} {' '.join(extra)}")
    seen: list[str] = []
    for word in words:
        low = word.lower()
        if len(low) < 4 or low in {"that", "this", "with", "from", "have",
                                   "were", "what", "when", "which", "their"}:
            continue
        if low not in seen:
            seen.append(low)
    return seen[:24]


def excerpt_windows(content: str, query: str, *,
                    max_chars: int = 6000, window: int = 1400) -> list[dict[str, Any]]:
    """Windows of the stored text most likely to contain the answer.

    Returned offsets are absolute positions in `content`, so a quote located in
    a window maps to the same absolute span as a quote located in the full text.
    """
    text = content or ""
    if len(text) <= max_chars:
        return [{"start": 0, "end": len(text), "text": text}]

    lowered = text.lower()
    anchors: list[int] = []
    for term in _terms(query):
        start = 0
        while len(anchors) < 60:
            found = lowered.find(term, start)
            if found < 0:
                break
            anchors.append(found)
            start = found + len(term)
    if not anchors:
        return [{"start": 0, "end": min(max_chars, len(text)),
                 "text": text[:max_chars]}]

    chosen: list[tuple[int, int]] = []
    for anchor in sorted(set(anchors)):
        start = max(0, anchor - window // 3)
        end = min(len(text), start + window)
        # Prefer a sentence boundary at the head; a quote that begins mid-word
        # is legal but useless to read.
        if start > 0:
            hit = _SENTENCE_END.search(text, start, min(start + 200, len(text)))
            if hit:
                start = hit.end()
        if chosen and start <= chosen[-1][1]:
            chosen[-1] = (chosen[-1][0], max(chosen[-1][1], end))
            continue
        chosen.append((start, end))
        if sum(e - s for s, e in chosen) >= max_chars:
            break

    windows = [{"start": s, "end": e, "text": text[s:e]} for s, e in chosen]
    budget = max_chars - sum(len(w["text"]) for w in windows)
    if budget > 400 and not windows:
        windows.append({"start": 0, "end": min(max_chars, len(text)),
                        "text": text[:max_chars]})
    return windows


def render_windows(content: str, query: str, *, max_chars: int = 6000) -> str:
    """Model input: numbered windows with their absolute offsets stated.

    Telling the model the offsets is not cosmetic. It is what makes an
    out-of-window quote detectable instead of silently accepted.
    """
    parts: list[str] = []
    for index, window in enumerate(excerpt_windows(content, query,
                                                   max_chars=max_chars), 1):
        parts.append(f"[window {index} | chars {window['start']}-{window['end']}]\n"
                     f"{window['text']}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Existing-source discovery
# ---------------------------------------------------------------------------

def find_existing_revisions(store: VerifyStore, *,
                            subject: str = "", predicate: str = "",
                            value_scope: str = "", limit: int = 5) -> list[dict[str, Any]]:
    """Find stored source revisions that may already cover this claim.

    Used before spending Tavily credits: if we already extracted a page that
    mentions the subject and predicate, reuse it instead of searching again.
    Relevance is heuristic (subject/predicate terms present in title, domain,
    or stored content) — final settlement is still done by the evidence gate.
    """
    terms = [t for t in (subject or "").split() + (predicate or "").split()
             if len(t) >= 3]
    if not terms:
        return []

    # Scan all sources/revisions for relevance. Keep it bounded.
    candidates: list[dict[str, Any]] = []
    for source in store.list_sources(limit=50):
        haystack = f"{source.get('title', '')} {source.get('canonical_url', '')} {source.get('domain', '')}".lower()
        if not any(t.lower() in haystack for t in terms):
            continue
        revisions = store.list_revisions_for_source(str(source["id"]), limit=3)
        for revision in revisions:
            content = str(revision.get("content") or "")
            content_lower = content.lower()
            score = sum(1 for t in terms if t.lower() in content_lower)
            if score > 0:
                candidates.append({
                    "source_id": source["id"],
                    "revision_id": revision["id"],
                    "canonical_url": source.get("canonical_url", ""),
                    "title": source.get("title", ""),
                    "content": content,
                    "published_date": revision.get("published_date"),
                    "relevance_score": score,
                })
    candidates.sort(key=lambda c: c["relevance_score"], reverse=True)
    return candidates[:limit]
