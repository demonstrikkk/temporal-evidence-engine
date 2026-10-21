"""Fetch one curated document, with a local cache.

The corpus is rebuilt rarely and the same 68 URLs are fetched every time, so a
cache is not an optimization here -- it is what keeps a rebuild from hammering
court servers. `refresh=True` is the explicit way to pay for fresh bytes.

Only `fetch_url` is fetched. `source_url` is often a human-facing handle page
(India Code) while `fetch_url` is the API or PDF the text actually comes from;
fetching both would double the load without adding evidence.
"""
from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
MIN_BYTES = 100


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()


def cache_paths(cache_dir: Path, doc_id: str) -> tuple[Path, Path]:
    return cache_dir / f"{doc_id}.bin", cache_dir / f"{doc_id}.json"


def fetch(document: dict, *, cache_dir: Path | None = None,
          refresh: bool = False, timeout: float = 90.0) -> dict:
    """Return `{content, content_type, status, fetched_at, from_cache, url}`.

    A non-200 response raises `RuntimeError`; the caller records the failure and
    fails the build rather than writing a corpus with a silent hole.
    """
    url = str(document.get("fetch_url") or "").strip()
    if not url:
        raise RuntimeError(f"{document.get('doc_id')}: no fetch_url")
    doc_id = str(document.get("doc_id") or "document")
    body_path, meta_path = (cache_paths(cache_dir, doc_id) if cache_dir
                            else (Path(), Path()))

    if cache_dir and not refresh and body_path.is_file() and meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        content = body_path.read_bytes()
        # The cache is a file (or two). A half-written body with matching
        # metadata would be parsed as if it were the source, so verify the
        # recorded digest before trusting it; a mismatch refetches.
        digest = hashlib.sha256(content).hexdigest()
        if meta.get("url") == url and meta.get("sha256") == digest:
            return {**meta, "content": content, "from_cache": True}

    with httpx.Client(headers={"User-Agent": UA, "Accept": "*/*"},
                      timeout=timeout, follow_redirects=True) as client:
        response = client.get(url)
    if response.status_code != 200:
        raise RuntimeError(f"{doc_id}: HTTP {response.status_code} from {url}")
    content = response.content
    if len(content) < MIN_BYTES:
        raise RuntimeError(f"{doc_id}: response is only {len(content)} bytes")

    meta = {
        "doc_id": doc_id,
        "url": url,
        "status": response.status_code,
        "content_type": response.headers.get("content-type", "").split(";")[0].strip(),
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "fetched_at": _now(),
        "from_cache": False,
    }
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(content)
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return {**meta, "content": content}
