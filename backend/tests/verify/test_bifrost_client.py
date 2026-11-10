"""The gateway client records what it spent, and keys the cache on what it asked for.

Three separate things live in `model_calls`, and the product's honesty depends
on keeping them apart:

* the **cache key**, which must be deterministic — derived from the request, not
  from what the gateway decided at runtime;
* the **audit row**, one per call, which is how the budget is enforced and how
  the gateway panel can prove a call happened at all;
* the **route**, meaning which provider and model actually answered.

Two shipped defects are pinned here. `CACHE_MODE=off` used to suppress the audit
row along with the cache, so a cache-off run reported zero calls and zero spend
while the gateway was answering every request — a budget control that silently
reads zero is not a budget control. And the row was stored under the *resolved*
model name, so once a fallback answered, the cache key no longer matched the
next identical request and `CACHE_MODE=read-only` could never replay a run.
"""
from __future__ import annotations

import httpx

from app.bifrost import client as client_mod
from app.bifrost.client import BifrostClient
from app.verify.store import LocalStore

REQUESTED = "groq/openai/gpt-oss-20b"
PROMPT = "extract these claims"
STAGE = "extract_claims"


class _Response:
    status_code = 200
    text = ""

    def __init__(self) -> None:
        self.headers = httpx.Headers({"x-bifrost-provider": "ollama"})

    def json(self) -> dict:
        # The gateway resolves the request to a different model: the primary
        # provider could not serve it, so a fallback answered.
        return {
            "id": "chatcmpl-test",
            "model": "llama3.2:1b",
            "choices": [{"message": {"content": '{"claims": []}'}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 4},
            "extra_fields": {"provider": "ollama", "cost": 0.0007},
        }


class _Http:
    """Stands in for `httpx.Client`: a context manager returning one response."""

    seen: list[dict] = []

    def __init__(self, *_, **__) -> None:  # noqa: D401 — mirrors httpx.Client
        pass

    def __enter__(self) -> "_Http":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def post(self, url: str, *, json: dict, headers: dict) -> _Response:
        _Http.seen.append({"url": url, "body": json, "headers": headers})
        return _Response()


def _client(tmp_path, cache_mode: str) -> BifrostClient:
    return BifrostClient(LocalStore(str(tmp_path)), "run-1",
                         base_url="http://127.0.0.1:8080",
                         virtual_key="sk-bf-test", cache_mode=cache_mode)


def _cache_key() -> str:
    return client_mod.prompt_hash("", PROMPT, REQUESTED, STAGE, 0.0, 1400)


def test_cache_off_still_records_the_call_and_the_cost(tmp_path, monkeypatch):
    monkeypatch.setattr(client_mod.httpx, "Client", _Http)
    _Http.seen.clear()
    client = _client(tmp_path, "off")

    first = client.complete(PROMPT, stage=STAGE, model=REQUESTED)
    second = client.complete(PROMPT, stage=STAGE, model=REQUESTED)

    # No replay: a cache-off run never reads a previous result.
    assert first.status == "live" and second.status == "live"
    assert len(_Http.seen) == 2

    rows = client.store.list_model_calls()
    assert len(rows) == 2, "both calls must be on the record, not just the first"
    assert {row["stage"] for row in rows} == {STAGE}
    assert all(row["bifrost_request_id"] == "chatcmpl-test" for row in rows)
    # The requested model is the key; the resolved model is the route.
    assert all(row["model"] == REQUESTED for row in rows)
    assert all(row["resolved_model"] == "llama3.2:1b" for row in rows)
    assert all(row["provider"] == "ollama" for row in rows)

    stats = client.store.gateway_stats(None)
    assert stats["calls"] == 2
    assert stats["input_tokens"] == 22
    assert stats["output_tokens"] == 8
    assert stats["cost_usd"] > 0, "spend must be visible to the budget check"
    assert stats["providers"] == ["ollama"]

    # The row is an audit fact, not a cache entry: nothing can replay it, even
    # when asked with the exact prompt that produced it.
    assert client.store.find_model_call(_cache_key(), REQUESTED, STAGE) is None


def test_read_write_caches_under_the_requested_model_and_labels_the_replay(
        tmp_path, monkeypatch):
    monkeypatch.setattr(client_mod.httpx, "Client", _Http)
    _Http.seen.clear()
    client = _client(tmp_path, "read-write")

    first = client.complete(PROMPT, stage=STAGE, model=REQUESTED)
    second = client.complete(PROMPT, stage=STAGE, model=REQUESTED)

    assert first.status == "live"
    assert second.status == "snapshot", "a replayed result must say so"
    assert second.created_at == first.created_at
    assert len(_Http.seen) == 1, "the second call must not touch the network"
    assert client.store.gateway_stats(None)["calls"] == 1

    # A snapshot reports the route that really answered, not the request.
    assert second.model == "llama3.2:1b"
    assert second.provider == "ollama"
    assert client.store.find_model_call(_cache_key(), REQUESTED, STAGE)


def test_a_read_only_run_replays_a_recorded_call(tmp_path, monkeypatch):
    """The demo path: one live run, then a run that cannot spend anything."""
    monkeypatch.setattr(client_mod.httpx, "Client", _Http)
    _Http.seen.clear()
    live = _client(tmp_path, "read-write")
    live.complete(PROMPT, stage=STAGE, model=REQUESTED)

    snapshot = _client(tmp_path, "read-only")
    replayed = snapshot.complete(PROMPT, stage=STAGE, model=REQUESTED)

    assert replayed.status == "snapshot"
    assert replayed.text == '{"claims": []}'
    assert len(_Http.seen) == 1, "a read-only run must never reach the network"


def test_a_read_only_run_refuses_to_invent_a_call(tmp_path, monkeypatch):
    monkeypatch.setattr(client_mod.httpx, "Client", _Http)
    _Http.seen.clear()
    client = _client(tmp_path, "read-only")

    try:
        client.complete("never asked before", stage=STAGE, model=REQUESTED)
    except client_mod.BifrostError as exc:
        assert exc.kind == "cache_miss"
    else:  # pragma: no cover — the assertion above is the point
        raise AssertionError("a cache miss must not silently become a live call")
    assert _Http.seen == []


def test_a_rate_limit_is_waited_out_for_as_long_as_it_asks(tmp_path, monkeypatch):
    """A 429 states its own window; retrying inside it burns the retries.

    The first live gold run lost a whole case because the retries fired within
    the same exhausted minute. `Retry-After` is honoured up to a cap, so a run
    waits out a token window but never hangs on a provider that asks for an hour.
    """

    class _Limited:
        status_code = 429
        text = '{"error": {"message": "rate limit"}}'

        def __init__(self, asked: str) -> None:
            self.headers = httpx.Headers({"retry-after": asked})

        def json(self) -> dict:  # pragma: no cover — never reached on a 429
            return {}

    class _Sequence:
        queue: list = []

        def __init__(self, *_, **__) -> None:
            pass

        def __enter__(self) -> "_Sequence":
            return self

        def __exit__(self, *exc_info) -> bool:
            return False

        def post(self, url: str, *, json: dict, headers: dict):
            return _Sequence.queue.pop(0)

    slept: list[float] = []
    monkeypatch.setattr(client_mod.httpx, "Client", _Sequence)
    monkeypatch.setattr(client_mod.time, "sleep", lambda seconds: slept.append(seconds))

    _Sequence.queue = [_Limited("3"), _Response()]
    client = _client(tmp_path, "off")
    assert client.complete(PROMPT, stage=STAGE, model=REQUESTED).status == "live"
    assert slept == [3.0], slept

    _Sequence.queue = [_Limited("3600"), _Response()]
    slept.clear()
    assert client.complete(PROMPT, stage=STAGE, model=REQUESTED).status == "live"
    assert slept == [float(client.retry_after_cap)], slept


def test_the_request_carries_both_authentication_headers(tmp_path, monkeypatch):
    """Bifrost only reads a key without the `sk-bf-` prefix from `x-bf-vk`."""
    monkeypatch.setattr(client_mod.httpx, "Client", _Http)
    _Http.seen.clear()
    _client(tmp_path, "off").complete(PROMPT, stage=STAGE, model=REQUESTED)

    sent = _Http.seen[0]["headers"]
    assert sent["Authorization"] == "Bearer sk-bf-test"
    assert sent["x-bf-vk"] == "sk-bf-test"
    assert _Http.seen[0]["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    assert _Http.seen[0]["body"]["model"] == REQUESTED
