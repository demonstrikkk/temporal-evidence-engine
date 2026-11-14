"""Playground routes, mounted and refusing correctly.

The important assertion here is the last one: a rejected request must not echo
the API key back. Pydantic's default 422 body includes the offending input, and
the custom handler in `main` replaces it — this test is what keeps that true for
the one route that receives a secret.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from app import main as main_mod  # noqa: E402

KEY = "sk-api-test-9876543210zyxwvu"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REQUIRE_KEYS_AT_STARTUP", "false")
    with TestClient(main_mod.app) as c:
        yield c


def test_the_route_is_mounted_and_lists_what_it_can_reach(client):
    response = client.get("/api/playground/presets")
    assert response.status_code == 200
    payload = response.json()
    assert payload["presets"], "a playground with no presets has no entry point"
    ids = {preset["id"] for preset in payload["presets"]}
    assert {"ollama", "openai", "custom"} <= ids
    assert isinstance(payload["tools_available"], bool)
    assert "writes nothing" in payload["note"]


def test_a_private_endpoint_is_refused_before_any_request_is_made(client):
    response = client.post(
        "/api/playground/chat",
        json={
            "base_url": "https://internal.example/v1",
            "model": "m",
            "api_key": KEY,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    # `internal.example` is a reserved name: either resolution fails or the
    # refusal names the private address. Both are a 422 and never a request.
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "E_VALIDATION"


def test_a_rejected_body_never_echoes_the_key(client):
    response = client.post(
        "/api/playground/chat",
        json={
            "base_url": "https://api.example/v1",
            "model": "m",
            "api_key": KEY,
            "messages": [],  # invalid: a chat needs at least one turn
        },
    )
    assert response.status_code == 422
    assert KEY not in response.text
    assert "messages" in response.json()["error"]["message"]


# --- the default route the page offers ----------------------------------------
#
# The gateway is the first preset when the deployment can reach it, and absent
# when it cannot. A preset that always fails is worse than no preset: it turns a
# configuration fact into an error the reader is invited to blame themselves for.


def _configure_gateway(monkeypatch, *, key="sk-bf-test-local"):
    from app.core import config as config_mod

    monkeypatch.setattr(config_mod.settings, "BIFROST_BASE_URL",
                        "http://127.0.0.1:8080", raising=False)
    monkeypatch.setattr(config_mod.settings, "BIFROST_VIRTUAL_KEY", key, raising=False)
    monkeypatch.setattr(config_mod.settings, "BIFROST_MODEL_EXTRACTOR",
                        "mistral/mistral-small-latest", raising=False)


def test_the_deployments_own_route_is_offered_first(client, monkeypatch):
    _configure_gateway(monkeypatch)
    presets = client.get("/api/playground/presets").json()["presets"]
    first = presets[0]
    assert first["id"] == "gateway"
    assert first["source"] == "gateway"
    assert first["model"] == "mistral/mistral-small-latest"
    # Nothing to paste: the credential is the deployment's and stays server-side.
    assert first["key_required"] is False
    assert first["base_url"] == ""
    assert all(preset["source"] == "endpoint" for preset in presets[1:])


def test_no_gateway_preset_without_a_credential(client, monkeypatch):
    _configure_gateway(monkeypatch, key="")
    ids = {preset["id"] for preset in client.get("/api/playground/presets").json()["presets"]}
    assert "gateway" not in ids


def test_a_gateway_request_reaches_the_client_without_a_url_or_key(client, monkeypatch):
    # The route hands the parsed body straight to the client, so this asserts
    # the shape that client receives. The call itself is replaced: a test that
    # posted a real gateway request would need a live Bifrost, a provider key
    # and a budget, which is not a unit test.
    from app.api import playground as playground_api

    seen: list = []

    async def fake_chat(body):
        seen.append(body)
        return {"answer": "ok", "source": body.source}

    monkeypatch.setattr(playground_api.playground_client, "chat", fake_chat)
    response = client.post(
        "/api/playground/chat",
        json={
            "source": "gateway",
            "model": "mistral/mistral-small-latest",
            "messages": [{"role": "user", "content": "hi"}],
            "allow_tools": False,
        },
    )
    assert response.status_code == 200
    assert seen and seen[0].source == "gateway"
    assert seen[0].base_url == ""
    assert seen[0].api_key == ""
