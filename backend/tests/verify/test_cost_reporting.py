"""A run must not report $0.00 for money it spent.

Measured on this machine: Bifrost answers `mistral/mistral-small-latest` with
`cost: null` while `GET /v1/models` publishes `pricing.prompt = 1e-7` and
`pricing.completion = 3e-7` for the same model. A 37k-token run therefore showed
`$0.00` when it had spent about half a cent. With Groq the same `$0.00` happened
to be true, which is what made the defect hard to see: the panel looked right.

The number alone cannot say which happened, so the row carries `cost_source`.
`gateway` is a bill, `catalogue` is an estimate, `none` is unpriced.
"""
from __future__ import annotations

import httpx
import pytest

from app.bifrost import client as client_mod
from app.bifrost.client import BifrostClient, _first_number
from app.bifrost.scheduler import scheduler
from app.verify.store import LocalStore


@pytest.fixture(autouse=True)
def _empty_pacer_window():
    """A fresh token window per test; the pacer is process-wide by design."""
    scheduler.limiter.reset()
    yield
    scheduler.limiter.reset()

STAGE = "propose_relationship"
MODEL = "mistral/mistral-small-latest"
PROMPT = "does this passage refute the claim"

CATALOGUE = {"data": [
    {"id": MODEL, "pricing": {"prompt": "0.0000001000",
                              "completion": "0.0000003000"}},
    {"id": "groq/openai/gpt-oss-20b",
     "pricing": {"prompt": "0.0000000750", "completion": "0.0000003000"}},
]}


class _Gateway:
    """httpx.Client stand-in that answers both the completion and the catalogue."""

    cost: float | None = None
    catalogue_hits = 0

    def __init__(self, *_, **__) -> None:
        pass

    def __enter__(self) -> "_Gateway":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def post(self, url: str, *, json: dict, headers: dict) -> "_Reply":
        extra = {"provider": "mistral"}
        if _Gateway.cost is not None:
            extra["cost"] = _Gateway.cost
        return _Reply({
            "id": "chatcmpl-cost",
            "model": "mistral-small-latest",
            "choices": [{"message": {"content": '{"relationship": "supports"}'}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
            "extra_fields": extra,
        })

    def get(self, url: str, *, headers: dict) -> "_Reply":
        _Gateway.catalogue_hits += 1
        return _Reply(CATALOGUE)


class _Reply:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = httpx.Headers({})
        self.text = ""

    def json(self) -> dict:
        return self._payload


def _client(tmp_path) -> BifrostClient:
    return BifrostClient(LocalStore(str(tmp_path)), "run-1",
                         base_url="http://127.0.0.1:8080",
                         virtual_key="sk-bf-test", cache_mode="off", retries=0)


def test_an_unpriced_completion_is_priced_from_the_gateway_catalogue(
        tmp_path, monkeypatch):
    _Gateway.cost = None
    _Gateway.catalogue_hits = 0
    monkeypatch.setattr(client_mod.httpx, "Client", _Gateway)

    result = _client(tmp_path).complete(PROMPT, stage=STAGE, model=MODEL)

    # 1,000 prompt tokens at 1e-7 + 200 completion tokens at 3e-7
    assert result.cost_usd == pytest.approx(0.00016)
    assert result.cost_source == "catalogue"
    assert _Gateway.catalogue_hits == 1


def test_a_gateway_reported_cost_is_never_relabelled(tmp_path, monkeypatch):
    _Gateway.cost = 0.0042
    _Gateway.catalogue_hits = 0
    monkeypatch.setattr(client_mod.httpx, "Client", _Gateway)

    result = _client(tmp_path).complete(PROMPT, stage=STAGE, model=MODEL)

    assert result.cost_usd == pytest.approx(0.0042)
    assert result.cost_source == "gateway"
    assert _Gateway.catalogue_hits == 0, "a reported cost needs no estimate"


def test_the_catalogue_is_fetched_once_per_model(tmp_path, monkeypatch):
    _Gateway.cost = None
    _Gateway.catalogue_hits = 0
    monkeypatch.setattr(client_mod.httpx, "Client", _Gateway)

    client = _client(tmp_path)
    client.complete(PROMPT, stage=STAGE, model=MODEL)
    client.complete(PROMPT + " again", stage=STAGE, model=MODEL)

    assert _Gateway.catalogue_hits == 1


def test_an_unreachable_catalogue_leaves_the_call_unpriced_not_failed(
        tmp_path, monkeypatch):
    class _Broken(_Gateway):
        def get(self, url: str, *, headers: dict) -> _Reply:
            raise httpx.ConnectError("catalogue down")

    _Gateway.cost = None
    monkeypatch.setattr(client_mod.httpx, "Client", _Broken)

    result = _client(tmp_path).complete(PROMPT, stage=STAGE, model=MODEL)

    assert result.text.startswith('{"relationship"')
    assert result.cost_source == "none"
    assert result.cost_usd == 0.0


def test_a_reported_zero_is_a_number_and_missing_is_not():
    assert _first_number(None, "", 0) == 0.0
    assert _first_number(None, "") is None
