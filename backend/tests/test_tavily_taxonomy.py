"""Tavily failures are classified by type, not by string.

The bug this pins: quota/plan refusals (HTTP 432/433) fell through to
`provider_transient`, so discovery retried a call that could never succeed and
burned its wall-clock budget doing it. The typed SDK errors make the distinction
free and exact.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.core.errors import AppError
from app.providers.search import tavily


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def keyed(monkeypatch):
    # The autouse fixture in conftest blanks the key; this test needs one present
    # so the adapter reaches the call instead of failing at the gate.
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tvly-test")


def _raising(exc: BaseException):
    def _boom(*_args, **_kwargs):
        raise exc
    return _boom


def test_missing_key_is_fatal_named():
    with pytest.raises(AppError) as ei:
        run(tavily.search("q", 1))
    assert ei.value.code == "E_PROVIDER_FATAL"


def test_usage_limit_is_fatal_not_retried(keyed, monkeypatch):
    from tavily.errors import UsageLimitExceededError
    monkeypatch.setattr(tavily, "_sync_search",
                        _raising(UsageLimitExceededError("plan limit exceeded")))
    with pytest.raises(AppError) as ei:
        run(tavily.search("q", 1))
    assert ei.value.code == "E_PROVIDER_FATAL"


def test_invalid_key_is_fatal(keyed, monkeypatch):
    from tavily.errors import InvalidAPIKeyError
    monkeypatch.setattr(tavily, "_sync_search",
                        _raising(InvalidAPIKeyError("bad key")))
    with pytest.raises(AppError) as ei:
        run(tavily.search("q", 1))
    assert ei.value.code == "E_PROVIDER_FATAL"


def test_forbidden_is_fatal(keyed, monkeypatch):
    from tavily.errors import ForbiddenError
    monkeypatch.setattr(tavily, "_sync_search",
                        _raising(ForbiddenError("403 forbidden")))
    with pytest.raises(AppError) as ei:
        run(tavily.search("q", 1))
    assert ei.value.code == "E_PROVIDER_FATAL"


def test_rate_limit_stays_transient_and_keeps_retry_after(keyed, monkeypatch):
    monkeypatch.setattr(
        tavily, "_sync_search",
        _raising(RuntimeError("429 rate limited; retry after: 7")))
    with pytest.raises(AppError) as ei:
        run(tavily.search("q", 1))
    assert ei.value.code == "E_PROVIDER_TRANSIENT"
    assert (ei.value.details or {}).get("retry_after") == 7


def test_retry_after_is_read_from_the_keyless_attribute():
    class Keyless(Exception):
        retry_after_seconds = 12
    assert tavily._retry_after(Keyless("slow down")) == 12


def test_result_keeps_score_and_publication_date(keyed, monkeypatch):
    def _fake(*_args, **_kwargs):
        return {"results": [{
            "title": "Acme",
            "url": "https://acme.example/a",
            "content": "Acme employs 40 people.",
            "score": 0.82,
            "published_date": "2024-03-01",
            "id": "res-1",
        }], "usage": {"credits": 1}, "request_id": "req-1"}

    monkeypatch.setattr(tavily, "_sync_search", _fake)
    out = run(tavily.search("acme", 3))
    assert out == [{"title": "Acme", "url": "https://acme.example/a",
                    "snippet": "Acme employs 40 people.", "score": 0.82,
                    "published_date": "2024-03-01", "id": "res-1"}]


def test_garbage_score_is_clamped_to_zero(keyed, monkeypatch):
    monkeypatch.setattr(tavily, "_sync_search", lambda *a, **k: {
        "results": [{"title": "x", "url": "https://x.example/a",
                     "content": "", "score": "not-a-number"}]})
    out = run(tavily.search("x", 1))
    assert out[0]["score"] == 0.0


def test_search_asks_for_published_dates_and_passes_the_window(keyed, monkeypatch):
    """`published_date` is opt-in on the API. Without the flag it is absent, so
    every cell's `valid_time` is empty and a temporal request has nothing to
    check. The date bounds are only sent when non-empty."""
    import tavily as sdk

    seen: dict = {}

    class FakeClient:
        def __init__(self, api_key):
            self.api_key = api_key

        def search(self, query, **kwargs):
            seen.update(kwargs)
            return {"results": []}

    monkeypatch.setattr(sdk, "TavilyClient", FakeClient)
    run(tavily.search("q", 3, start_date="2020-01-01", end_date="2023-12-31"))
    assert seen["include_published_date"] is True
    assert seen["start_date"] == "2020-01-01"
    assert seen["end_date"] == "2023-12-31"


def test_search_omits_date_bounds_when_none_were_requested(keyed, monkeypatch):
    import tavily as sdk

    seen: dict = {}

    class FakeClient:
        def __init__(self, api_key):
            self.api_key = api_key

        def search(self, query, **kwargs):
            seen.update(kwargs)
            return {"results": []}

    monkeypatch.setattr(sdk, "TavilyClient", FakeClient)
    run(tavily.search("q", 3))
    assert "start_date" not in seen
    assert "end_date" not in seen


# -- domain filters ------------------------------------------------------------
#
# The API rejects a request that carries both `include_domains` and
# `exclude_domains`. The run sizes its exclude list from hosts that blocked us
# before, so a plan with an allowlist and one bad host supplied both, and every
# search in that run came back 400 — which is classified as fatal, so discovery
# stopped entirely rather than losing one host.

def _recording_client(monkeypatch, results):
    import tavily as sdk

    seen: dict = {}

    class FakeClient:
        def __init__(self, api_key):
            self.api_key = api_key

        def search(self, query, **kwargs):
            seen.update(kwargs)
            return {"results": results}

    monkeypatch.setattr(sdk, "TavilyClient", FakeClient)
    return seen


def test_both_filters_never_travel_to_the_provider_together(keyed, monkeypatch):
    seen = _recording_client(monkeypatch, [
        {"url": "https://keep.example/a", "content": "x"},
        {"url": "https://blocked.example/b", "content": "y"},
    ])
    out = run(tavily.search("q", 5, include_domains=["keep.example", "blocked.example"],
                            exclude_domains=["blocked.example"]))
    assert seen.get("include_domains") == ["keep.example", "blocked.example"]
    assert "exclude_domains" not in seen, "sending both is a 400 on this API"
    assert [r["url"] for r in out] == ["https://keep.example/a"]


def test_a_subdomain_of_an_excluded_host_is_dropped():
    assert tavily._blocked("https://news.example.com/a", ["example.com"]) is True
    assert tavily._blocked("https://example.com/a", ["example.com"]) is True
    assert tavily._blocked("https://notexample.com/a", ["example.com"]) is False


def test_an_exclude_list_alone_is_sent_to_the_provider(keyed, monkeypatch):
    seen = _recording_client(monkeypatch, [])
    run(tavily.search("q", 3, exclude_domains=["blocked.example"]))
    assert seen.get("exclude_domains") == ["blocked.example"]
    assert "include_domains" not in seen


def test_an_include_list_alone_is_sent_to_the_provider(keyed, monkeypatch):
    seen = _recording_client(monkeypatch, [])
    run(tavily.search("q", 3, include_domains=["keep.example"]))
    assert seen.get("include_domains") == ["keep.example"]
    assert "exclude_domains" not in seen
