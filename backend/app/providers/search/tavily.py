"""Tavily discovery adapter. Blocking SDK isolated via to_thread + timeout (docs/35).

Two things this adapter refuses to do any more.

**Guess from the message whether a failure is retryable.** `tavily-python`
publishes typed errors (`InvalidAPIKeyError`, `ForbiddenError`,
`UsageLimitExceededError`, `BadRequestError`). The old code string-matched the
message, so a quota/plan refusal (HTTP 432/433) fell through to
`provider_transient` and discovery retried it every round — burning the whole
wall clock on a call that could never succeed. Typed classes are classified
first; the message scan survives only as a fallback for SDK versions that raise
untyped errors.

**Throw away what the engine already told us.** `score` is a free relevance
prior and `published_date` is the only date the search API exposes; both used to
be dropped before ranking saw them. They are carried now. `score` is bounded by
the caller (`agents.graph._plan_relevance`) so it can never outrank a candidate
that names the entity itself.
"""
import asyncio
import re
from typing import Any

from app.core.config import settings
from app.core.errors import AppError, provider_fatal, provider_transient
from app.core.logging import log

try:  # pragma: no cover - shape is stable across the versions we support
    from tavily.errors import (BadRequestError, ForbiddenError,
                               InvalidAPIKeyError, UsageLimitExceededError)
    #: Refusals that retrying cannot fix: bad key, forbidden plan, exhausted
    #: quota, or a request the API rejects outright.
    _FATAL_ERRORS: tuple[type[BaseException], ...] = (
        BadRequestError, ForbiddenError, InvalidAPIKeyError,
        UsageLimitExceededError)
except Exception:  # SDK absent or renamed the classes; the message scan handles it
    _FATAL_ERRORS = ()

_RETRY_AFTER = re.compile(r"retry[\s_-]*after[:\s]+(\d+)", re.I)


def _retry_after(exc: BaseException) -> int | None:
    """Seconds the provider asked us to wait, when it said so.

    The keyless limit error exposes it as an attribute; HTTP errors that carry a
    ``Response`` expose it as a header. Neither is guaranteed, so both are
    probed and the exception message is the last resort.
    """
    value: Any = getattr(exc, "retry_after_seconds", None)
    if value is None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if headers is not None:
            value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        hit = _RETRY_AFTER.search(str(exc))
        value = hit.group(1) if hit else None
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None


def _classify(exc: BaseException) -> AppError:
    """Map a provider exception onto the app's two provider error kinds.

    Typed SDK errors win. A typed refusal is *fatal*: the run cannot recover by
    trying the same key again, so it stops with a reason instead of retrying.
    Everything untyped keeps the old message scan, plus `Retry-After` when the
    provider supplied a number.
    """
    if isinstance(exc, AppError):
        return exc
    if _FATAL_ERRORS and isinstance(exc, _FATAL_ERRORS):
        return provider_fatal(
            f"Tavily refused the request (key, plan or quota): {str(exc)[:150]}")
    msg = str(exc)
    low = msg.lower()
    if "401" in msg or "403" in msg or "unauthorized" in low or "invalid api key" in low:
        return provider_fatal(f"Tavily auth failed: {msg[:150]}")
    if "432" in msg or "433" in msg:
        # Plan or pay-as-you-go limit exhausted. A 429 is a rate limit and is
        # handled below; conflating the two is what made the old code retry a
        # hard refusal.
        return provider_fatal(f"Tavily quota exhausted: {msg[:150]}")
    if "429" in msg or "rate limit" in low:
        wait = _retry_after(exc)
        detail = {"retry_after": wait} if wait is not None else {}
        return provider_transient(f"Tavily rate-limited: {msg[:150]}", detail)
    return provider_transient(f"Tavily error: {msg[:150]}")


def _sync_search(api_key: str, query: str, limit: int, depth: str, topic: str,
                 include: list[str], exclude: list[str],
                 start_date: str = "", end_date: str = "") -> dict:
    from tavily import TavilyClient
    kwargs: dict = {}
    # The API does not accept both filters in one request. When a plan names
    # domains to include and the run has also learned a host is blocked, the
    # include list is the stronger constraint, so it is what gets sent; the
    # exclusion is applied to the results instead (`search`). Sending both is a
    # 400, which `_classify` correctly calls fatal — one blocked host would then
    # kill discovery for every query that had an allowlist.
    if include:
        kwargs["include_domains"] = include[:300]
    elif exclude:
        kwargs["exclude_domains"] = exclude[:150]
    # Only sent when the request actually named a time. Tavily accepts
    # `YYYY-MM-DD`; an empty string is not the same as "no constraint" and the
    # SDK forwards it, so the keys are omitted rather than blanked.
    if start_date:
        kwargs["start_date"] = start_date
    if end_date:
        kwargs["end_date"] = end_date
    return TavilyClient(api_key=api_key).search(
        query, search_depth=depth, max_results=max(1, min(limit, 20)),
        topic=topic, include_answer=False,
        # `published_date` is only returned when asked for (the SDK signature
        # does not expose this flag, so it travels through **kwargs). Without it
        # every cell's `valid_time` was empty and a temporal request had nothing
        # to check against. It costs no extra credits.
        include_published_date=True,
        # Ask for the per-call receipt (credits + request id). Without this the
        # only record of what a search cost is our own estimate.
        include_usage=True, **kwargs)


def _score(value: Any) -> float:
    """Tavily's relevance score, clamped to [0, 1]. Missing/garbage -> 0."""
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _host(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return (urlparse(str(url or "")).hostname or "").lower().removeprefix("www.")
    except Exception:  # noqa: BLE001 (a malformed URL is simply not a match)
        return ""


def _blocked(url: str, excluded: list[str]) -> bool:
    """Is `url` on a host the caller excluded?

    A subdomain counts: excluding `example.com` has to exclude
    `news.example.com`, or a block list that names the registrable domain does
    nothing. Exact host comparison alone silently let it through.
    """
    host = _host(url)
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in excluded if d)


async def search(query: str, limit: int = 5, include_domains: list[str] | None = None,
                 exclude_domains: list[str] | None = None,
                 start_date: str = "", end_date: str = "") -> list[dict[str, Any]]:
    """Returns [{title, url, snippet, score, published_date, id}].

    Raises AppError (never raw SDK errors). `score` is the engine's own relevance
    in [0, 1]; `published_date` is ISO-ish text and empty when the engine did not
    supply one. `start_date`/`end_date` are `YYYY-MM-DD` bounds and are sent only
    when non-empty.
    """
    key = settings.TAVILY_API_KEY or ""
    if not key:
        raise provider_fatal("Discovery unavailable: TAVILY_API_KEY unset")
    q = (query or "")[:300]
    if not q.strip():
        raise provider_fatal("Discovery refused: empty query")
    try:
        res = await asyncio.wait_for(
            asyncio.to_thread(_sync_search, key, q, limit,
                              settings.TAVILY_SEARCH_DEPTH, settings.TAVILY_TOPIC,
                              include_domains or [], exclude_domains or [],
                              start_date, end_date),
            timeout=30)
    except (asyncio.TimeoutError, TimeoutError) as e:
        raise provider_transient(f"Tavily timeout: {e}")
    except Exception as e:
        raise _classify(e) from e

    # Read the receipt while it is in hand. `usage.credits` is the provider's
    # number, not our projection, so a mismatch shows up in the logs instead of
    # silently becoming a wrong invoice.
    usage = res.get("usage") or {}
    log.info("tavily search", extra={"data": {
        "query": q[:120],
        "results": len(res.get("results") or []),
        "credits": usage.get("credits"),
        "request_id": res.get("request_id") or "",
    }})

    out = []
    for r in (res.get("results", []) or [])[:limit]:
        url = str(r.get("url", ""))[:2000]
        if url.startswith(("http://", "https://")):
            out.append({"title": str(r.get("title", ""))[:300],
                        "url": url,
                        "snippet": str(r.get("content", ""))[:2000],
                        "score": _score(r.get("score")),
                        "published_date": str(r.get("published_date") or "")[:100],
                        "id": str(r.get("id") or "")[:200]})
    # When the request carried both an allowlist and a block list the exclusion
    # could not be sent to the API (see `_sync_search`), so it is enforced here.
    # A no-op on the ordinary one-list path: the provider already honoured it.
    blocked = [str(d).strip().lstrip(".").lower()
               for d in (exclude_domains or []) if str(d).strip()]
    if blocked:
        kept = [r for r in out if not _blocked(r["url"], blocked)]
        if len(kept) != len(out):
            log.info("tavily dropped excluded hosts from results",
                     extra={"data": {"dropped": len(out) - len(kept),
                                     "keep": len(kept)}})
        out = kept
    return out
