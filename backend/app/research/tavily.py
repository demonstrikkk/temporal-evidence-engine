"""Tavily: discovery and extraction, both cached, neither trusted.

Two API behaviours are treated as part of the contract because ignoring them is
how a verifier quietly fabricates evidence:

* **HTTP 200 does not mean every URL worked.** Both `/search` and `/extract`
  return a `failed_results` array alongside the successful ones. A run that only
  reads `results` reports success while silently dropping half its sources.
  Every failure is returned to the caller, which emits `source.failed`.
* **A search snippet is not source text.** `content` from `/search` is a
  snippet the engine wrote. Only `/extract` (or the guarded crawler fallback)
  produces text that gets stored as a `source_revisions` row. Passing a snippet
  to `settle` would locate a quote in text the store does not hold.

The cache is keyed by `hash(tool, request-without-credentials)`, so a replay is
byte-identical to the original response and can be labelled `snapshot`.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from app.cache.coordinator import CacheCoordinator
from app.cache.keys import source_snapshot_key, tavily_query_key
from app.core.config import settings
from app.core.logging import log
from app.verify.store import VerifyStore, iso, now

Kind = Literal["transient", "fatal", "cache_miss"]


class ResearchError(RuntimeError):
    def __init__(self, kind: Kind, message: str, *,
                 status: int | None = None,
                 detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.detail = detail or {}

    @property
    def retryable(self) -> bool:
        return self.kind == "transient"


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    score: float
    published_date: str | None = None


@dataclass(frozen=True)
class FailedUrl:
    url: str
    error: str


@dataclass(frozen=True)
class SearchOutcome:
    query: str
    hits: list[SearchHit]
    failed: list[FailedUrl]
    request_id: str
    credits: int
    status: Literal["live", "snapshot"]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractOutcome:
    pages: dict[str, str]              # url -> full extracted text
    failed: list[FailedUrl]
    request_id: str
    credits: int
    status: Literal["live", "snapshot"]
    raw: dict[str, Any] = field(default_factory=dict)


def cache_key(tool: str, payload: dict[str, Any]) -> str:
    clean = {k: v for k, v in payload.items() if k != "api_key"}
    blob = json.dumps({"tool": tool, "payload": clean}, sort_keys=True,
                      ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class TavilyClient:
    def __init__(self, store: VerifyStore, *, api_key: str | None = None,
                 base_url: str | None = None, timeout: int | None = None,
                 cache_mode: str | None = None) -> None:
        self.store = store
        self.api_key = api_key if api_key is not None else settings.TAVILY_API_KEY
        self.base_url = (base_url or settings.TAVILY_BASE_URL).rstrip("/")
        self.timeout = timeout or settings.TAVILY_TIMEOUT_S
        self.cache_mode = cache_mode or settings.CACHE_MODE
        self.cache = CacheCoordinator(store)

    def configured(self) -> bool:
        return bool(self.api_key)

    # -- cache ------------------------------------------------------------
    def _cached(self, tool: str, payload: dict[str, Any]) -> dict | None:
        if self.cache_mode == "off":
            return None
        row = self.store.cache_get(cache_key(tool, payload))
        return (row or {}).get("payload")

    def _store(self, tool: str, payload: dict[str, Any], response: dict) -> None:
        if self.cache_mode != "off":
            self.store.cache_put(cache_key(tool, payload), tool, response)

    def _post(self, tool: str, payload: dict[str, Any]) -> tuple[dict, bool]:
        cached = self._cached(tool, payload)
        if cached is not None:
            return cached, True
        if self.cache_mode == "read-only":
            raise ResearchError(
                "cache_miss",
                f"CACHE_MODE=read-only but no recorded {tool} call for "
                f"{payload.get('query') or payload.get('urls')}. A snapshot run "
                f"cannot invent research.")
        if not self.configured():
            raise ResearchError("fatal",
                                "Tavily is not configured: TAVILY_API_KEY is empty.")
        body = {**payload, "api_key": self.api_key}
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(f"{self.base_url}/{tool}", json=body,
                                       headers=headers)
        except httpx.TimeoutException as exc:
            raise ResearchError("transient", f"Tavily {tool} timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ResearchError("transient",
                                f"Tavily {tool} transport error: {exc}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code in (401, 403):
            raise ResearchError("fatal",
                                f"Tavily refused the key ({response.status_code}).",
                                status=response.status_code)
        if response.status_code in (432, 433):
            raise ResearchError("fatal",
                                f"Tavily plan or credit limit reached "
                                f"({response.status_code}).",
                                status=response.status_code)
        if response.status_code == 429 or response.status_code >= 500:
            raise ResearchError("transient",
                                f"Tavily answered {response.status_code}. Retryable.",
                                status=response.status_code,
                                detail={"retry_after":
                                        response.headers.get("retry-after")})
        if response.status_code >= 400:
            raise ResearchError("fatal",
                                f"Tavily rejected the {tool} request "
                                f"({response.status_code}): {response.text[:200]}",
                                status=response.status_code)
        try:
            data = response.json()
        except ValueError as exc:
            raise ResearchError("transient",
                                "Tavily returned non-JSON.") from exc
        data["_latency_ms"] = latency_ms
        data["_fetched_at"] = iso(now())
        self._store(tool, payload, data)
        log.info("tavily call", extra={"data": {
            "tool": tool, "latency_ms": latency_ms,
            "request_id": data.get("request_id") or "",
            "credits": (data.get("usage") or {}).get("credits")}})
        return data, False

    # -- search -----------------------------------------------------------
    def search(self, query: str, *, max_results: int | None = None,
               include_domains: list[str] | None = None,
               exclude_domains: list[str] | None = None,
               start_date: str = "", end_date: str = "") -> SearchOutcome:
        query = (query or "").strip()[:400]
        if not query:
            raise ResearchError("fatal", "Tavily search refused: empty query.")
        payload: dict[str, Any] = {
            "query": query,
            "search_depth": settings.TAVILY_SEARCH_DEPTH,
            "max_results": max(1, min(max_results or settings.TAVILY_MAX_RESULTS, 20)),
            "topic": "general",
            "include_answer": False,
            # A snippet is not evidence; raw content is not stored either. What
            # this flag buys is a better excerpt for the *query planner*, never
            # a replacement for extract().
            "include_raw_content": False,
            "include_usage": True,
        }
        if include_domains:
            payload["include_domains"] = include_domains[:20]
        elif exclude_domains:
            payload["exclude_domains"] = exclude_domains[:20]
        if start_date:
            payload["start_date"] = start_date
        if end_date:
            payload["end_date"] = end_date

        # L3: Check CacheCoordinator Tavily query cache before calling API
        if self.cache_mode != "off":
            l3_key = tavily_query_key(
                query, settings.TAVILY_SEARCH_DEPTH,
                include_domains=include_domains,
                max_results=max_results or settings.TAVILY_MAX_RESULTS)
            cached_l3 = self.cache.get_l3(
                query=query, search_depth=settings.TAVILY_SEARCH_DEPTH,
                include_domains=include_domains,
                max_results=max_results or settings.TAVILY_MAX_RESULTS)
            if cached_l3:
                log.info("tavily l3 cache hit", extra={"data": {
                    "query": query[:80], "key": l3_key[:16]}})
                data = cached_l3
                replayed = True
            else:
                data, replayed = self._post("search", payload)
                # L3: Write successful search results to cache
                self.cache.put_l3(
                    query=query, payload=data,
                    search_depth=settings.TAVILY_SEARCH_DEPTH,
                    include_domains=include_domains,
                    max_results=max_results or settings.TAVILY_MAX_RESULTS)
        else:
            data, replayed = self._post("search", payload)
        hits = []
        for item in data.get("results") or []:
            url = str(item.get("url") or "")[:2000]
            if not url.startswith(("http://", "https://")):
                continue
            hits.append(SearchHit(
                title=str(item.get("title") or "")[:300],
                url=url,
                snippet=str(item.get("content") or "")[:2000],
                score=float(item.get("score") or 0.0),
                published_date=str(item.get("published_date") or "")[:100] or None,
            ))
        failed = [FailedUrl(str(f.get("url") or "")[:2000],
                            str(f.get("error") or "unknown")[:300])
                  for f in data.get("failed_results") or []]
        usage = data.get("usage") or {}
        return SearchOutcome(
            query=query, hits=hits, failed=failed,
            request_id=str(data.get("request_id") or ""),
            credits=int(usage.get("credits") or 0),
            status="snapshot" if replayed else "live", raw=data)

    # -- extract ----------------------------------------------------------
    def extract(self, urls: list[str], *, depth: str = "advanced") -> ExtractOutcome:
        urls = [u for u in urls if u.startswith(("http://", "https://"))][:20]
        if not urls:
            return ExtractOutcome({}, [], "", 0, "live", {})
        payload: dict[str, Any] = {
            "urls": urls,
            "extract_depth": depth,
            "format": "markdown",
            "include_images": False,
            "include_usage": True,
        }
        data, replayed = self._post("extract", payload)
        pages: dict[str, str] = {}
        for item in data.get("results") or []:
            url = str(item.get("url") or "")[:2000]
            text = str(item.get("raw_content") or "")
            if url and text.strip():
                pages[url] = text
        failed = [FailedUrl(str(f.get("url") or "")[:2000],
                            str(f.get("error") or "unknown")[:300])
                  for f in data.get("failed_results") or []]
        usage = data.get("usage") or {}
        return ExtractOutcome(
            pages=pages, failed=failed,
            request_id=str(data.get("request_id") or ""),
            credits=int(usage.get("credits") or 0),
            status="snapshot" if replayed else "live", raw=data)
