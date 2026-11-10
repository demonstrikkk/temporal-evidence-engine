"""`GET /api/health` must say whether the store answers, and which model rung serves.

The failure these lock in: the endpoint reported the *configured* adapter name
and issued no query, so a pooler that had stopped resolving DNS -- or a local
root directory that was cleaned up -- presented as a healthy API returning 503
on every other route. Separately, "is Groq being used?" was only answerable by
reading `.env` and knowing that `OPENCODE_STRICT` removes the Groq rung, which
is exactly the kind of fact a health payload should carry.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from app import main as main_mod  # noqa: E402
from app.core import config as config_mod  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_mod.settings, "OPENCODE_ENABLED", False)
    with TestClient(main_mod.app) as c:
        yield c


def _data(client, headers=None):
    r = client.get("/api/health", headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_a_reachable_store_is_reported_as_reachable(client):
    data = _data(client)
    assert data["status"] == "ok"
    assert data["persistence"]["reachable"] is True
    assert "error" not in data["persistence"]


def test_a_dead_store_is_not_a_healthy_api(client, monkeypatch):
    class Dead:
        def ping(self):
            raise RuntimeError("could not translate host name to address")

    monkeypatch.setattr(main_mod, "REPO", Dead())
    data = _data(client)
    assert data["status"] == "degraded"
    assert data["persistence"]["reachable"] is False
    assert "translate host name" in data["persistence"]["error"]


def test_a_hanging_store_cannot_hang_the_health_check(client, monkeypatch):
    """A black-holed database must not stall the check that exists to notice it."""
    import time as time_mod

    class Hanging:
        def ping(self):
            time_mod.sleep(2)
            return None

    real_wait_for = asyncio.wait_for

    async def _fast(coro, timeout):
        return await real_wait_for(coro, 0.05)

    monkeypatch.setattr(main_mod, "REPO", Hanging())
    monkeypatch.setattr(main_mod.asyncio, "wait_for", _fast)
    started = time_mod.monotonic()
    data = _data(client)
    assert time_mod.monotonic() - started < 1.5
    assert data["status"] == "degraded"
    assert data["persistence"]["reachable"] is False


def test_strict_mode_says_groq_cannot_serve(client, monkeypatch):
    monkeypatch.setattr(config_mod.settings, "OPENCODE_STRICT", True)
    monkeypatch.setattr(config_mod.settings, "GROQ_API_KEY", "test-key")
    chain = _data(client)["llm"]
    assert chain["strict"] is True
    assert chain["fallback_allowed"] is False
    assert chain["groq"]["configured"] is True
    assert chain["groq"]["in_chain"] is False, (
        "OPENCODE_STRICT removes the Groq rung; health must not imply otherwise")
    assert "groq" not in chain["rungs"]


def test_non_strict_mode_names_groq_as_the_first_rung(client, monkeypatch):
    monkeypatch.setattr(config_mod.settings, "OPENCODE_STRICT", False)
    monkeypatch.setattr(config_mod.settings, "OPENCODE_ENABLED", False)
    monkeypatch.setattr(config_mod.settings, "GROQ_API_KEY", "test-key")
    chain = _data(client)["llm"]
    assert chain["opencode"]["configured"] is False
    assert chain["first_rung"] == "groq"
    assert chain["rungs"][0] == "groq"
    assert chain["groq"]["in_chain"] is True


def test_an_anonymous_caller_learns_nothing_about_the_deployment(client, monkeypatch):
    monkeypatch.setattr(config_mod.settings, "ALLOW_UNAUTHENTICATED", False)
    monkeypatch.setattr(config_mod.settings, "API_KEY", "secret")
    data = _data(client)
    assert data["status"] == "ok"
    assert "persistence" not in data and "llm" not in data
    assert data["detail"] == "authenticated"
