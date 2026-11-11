"""A provider that rejects its own generation is not a request we sent wrong.

Measured on Groq, twice in one thirty-call run:

    HTTP 400  {"error": {"code": "json_validate_failed",
                         "message": "Failed to validate JSON."}}

That arrives when the provider's constrained decoder rejects the *model's*
output. It was classified `fatal`, so the source was dropped without a second
attempt. The same prompt answered correctly on a retry, which makes this a
retryable failure wearing a 4xx, not a malformed request from us.

The opposite case matters just as much: an unknown model is a real 400, and
retrying it only spends the run's time.
"""
from __future__ import annotations

import httpx
import pytest

from app.bifrost import client as client_mod
from app.bifrost.client import BifrostClient, BifrostError
from app.verify.store import LocalStore

STAGE = "propose_relationship"
MODEL = "mistral/mistral-small-latest"
PROMPT = "compare this claim with this source"


class _Refusal:
    """One provider refusal, with the body that decides its classification."""

    text = ""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.text = body
        self.headers = httpx.Headers({})

    def json(self) -> dict:  # pragma: no cover - never reached on a refusal
        return {}


class _Refusing:
    """httpx.Client stand-in that answers every POST with the same refusal."""

    hits: list[int] = []
    status_code = 400
    body = ""

    def __init__(self, *_, **__) -> None:
        pass

    def __enter__(self) -> "_Refusing":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def post(self, url: str, *, json: dict, headers: dict) -> _Refusal:
        _Refusing.hits.append(1)
        return _Refusal(_Refusing.status_code, _Refusing.body)


def _client(tmp_path) -> BifrostClient:
    return BifrostClient(LocalStore(str(tmp_path)), "run-1",
                         base_url="http://127.0.0.1:8080",
                         virtual_key="sk-bf-test", cache_mode="off", retries=1)


def test_a_rejected_generation_is_retried_once_before_it_is_reported(
        tmp_path, monkeypatch):
    _Refusing.hits.clear()
    _Refusing.status_code = 400
    _Refusing.body = (
        '{"is_bifrost_error":false,"status_code":400,"error":{"type":'
        '"invalid_request_error","code":"json_validate_failed","message":'
        '"Failed to generate JSON. Please adjust your prompt."}}')
    monkeypatch.setattr(client_mod.httpx, "Client", _Refusing)
    monkeypatch.setattr(client_mod, "_retry_delay", lambda *a, **k: 0.0)

    with pytest.raises(BifrostError) as caught:
        _client(tmp_path).complete(PROMPT, stage=STAGE, model=MODEL)

    assert caught.value.kind == "transient", \
        "a provider-side generation failure is retryable, not our bad request"
    assert len(_Refusing.hits) == 2, "one attempt plus exactly one retry"


def test_an_unknown_model_is_still_fatal_and_not_retried(tmp_path, monkeypatch):
    _Refusing.hits.clear()
    _Refusing.status_code = 400
    _Refusing.body = (
        '{"is_bifrost_error":true,"error":{"type":"invalid_request_error",'
        '"code":"model_not_found","message":"Unknown model"}}')
    monkeypatch.setattr(client_mod.httpx, "Client", _Refusing)
    monkeypatch.setattr(client_mod, "_retry_delay", lambda *a, **k: 0.0)

    with pytest.raises(BifrostError) as caught:
        _client(tmp_path).complete(PROMPT, stage=STAGE, model=MODEL)

    assert caught.value.kind == "fatal"
    assert len(_Refusing.hits) == 1, "retrying a bad request only burns run time"
