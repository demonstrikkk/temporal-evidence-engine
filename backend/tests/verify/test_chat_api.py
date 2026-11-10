"""Test suite for Chat, Governance, and Cache API endpoints."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.verify.runtime import get_store


@pytest.fixture
def client(monkeypatch, tmp_path):
    # Enable unauthenticated access and local persistence for test client
    monkeypatch.setattr("app.core.config.settings.ALLOW_UNAUTHENTICATED", True)
    monkeypatch.setattr("app.core.config.settings.PERSISTENCE", "local")
    monkeypatch.setattr("app.core.config.settings.TEV_LOCAL_DIR", str(tmp_path))
    return TestClient(app)


def test_governance_quota_endpoint(client):
    res = client.get("/api/governance/quota")
    assert res.status_code == 200
    data = res.json()
    assert "quota" in data
    assert "tavily" in data["quota"]
    assert "current_budget_mode" in data


def test_governance_budget_mode_endpoint(client):
    res = client.post("/api/governance/budget-mode", json={"mode": "conserve"})
    assert res.status_code == 200
    assert res.json()["mode"] == "conserve"

    # Reset back to balanced
    res2 = client.post("/api/governance/budget-mode", json={"mode": "balanced"})
    assert res2.status_code == 200
    assert res2.json()["mode"] == "balanced"


def test_cache_stats_and_purge_endpoint(client):
    res = client.get("/api/cache/stats")
    assert res.status_code == 200
    assert "stats" in res.json()

    purge_res = client.post("/api/cache/purge", json={"cache_type": "l1_calc"})
    assert purge_res.status_code == 200
    assert purge_res.json()["status"] == "purged"


def test_chat_conversation_lifecycle(client):
    # 1. Create conversation
    create_res = client.post("/api/chat/conversations", json={
        "title": "Pricing Investigation",
        "budget_mode": "balanced",
    })
    assert create_res.status_code == 200
    conv = create_res.json()["conversation"]
    conv_id = conv["id"]
    assert conv["title"] == "Pricing Investigation"

    # 2. List conversations
    list_res = client.get("/api/chat/conversations")
    assert list_res.status_code == 200
    assert any(c["id"] == conv_id for c in list_res.json()["conversations"])

    # 3. Get single conversation
    get_res = client.get(f"/api/chat/conversations/{conv_id}")
    assert get_res.status_code == 200
    assert get_res.json()["conversation"]["id"] == conv_id
    assert get_res.json()["messages"] == []
