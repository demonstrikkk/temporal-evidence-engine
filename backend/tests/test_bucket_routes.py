"""The bucket route groups a dataset by the plan's edition rules."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from app import main as main_mod  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REQUIRE_KEYS_AT_STARTUP", "false")
    from app.core.config import settings as _settings
    monkeypatch.setattr(_settings, "OPENCODE_ENABLED", False)
    with TestClient(main_mod.app) as c:
        yield c


def _dataset(*, buckets: list[dict] | None = None,
             records: int = 2) -> str:
    plan = {
        "goal": "legal documents", "entity": "legal_document",
        "fields": [{"name": "jurisdiction", "type": "string"}],
        "search_queries": ["legal"],
        "buckets": buckets if buckets is not None else [
            {"name": "regional", "label": "Regional", "field": "jurisdiction",
             "values": ["Delhi"]},
            {"name": "national", "label": "National", "field": "jurisdiction",
             "values": ["India"]},
        ],
    }
    rows = [
        {"fields": {"jurisdiction": {"value": "India" if i % 2 == 0 else "Delhi",
                                     "verification_status": "verified",
                                     "source": {}}}}
        for i in range(records)
    ]
    run_id = "run-buckets-test"
    main_mod.REPO.create_run(run_id, "legal_corpus", plan)
    main_mod.REPO.save_plan(run_id, plan)
    return main_mod.REPO.finalize_dataset(run_id, plan, rows, {"records": 2})


def test_buckets_group_records_and_keep_record_ids(client):
    did = _dataset()
    resp = client.get(f"/api/datasets/{did}/buckets")

    assert resp.status_code == 200
    data = resp.json()["data"]
    views = {b["name"]: b for b in data["buckets"]}
    assert views["national"]["count"] == 1
    assert views["regional"]["count"] == 1
    assert data["unassigned_count"] == 0


def test_buckets_page_past_the_repository_page_cap(client):
    did = _dataset(records=505)
    data = client.get(f"/api/datasets/{did}/buckets").json()["data"]

    assert data["records"] == 505
    views = {b["name"]: b for b in data["buckets"]}
    assert views["national"]["count"] + views["regional"]["count"] == 505
    assert data["unassigned_count"] == 0


def test_a_compiled_run_finds_its_plan_under_the_plan_id(client):
    """A run started from a compiled plan stores the plan under the plan id.

    Looking only under the run id made every compiled run's dataset report
    "no buckets" for a plan that declared them.
    """
    plan_id = "plan-buckets-compiled"
    plan = {
        "goal": "legal documents", "entity": "legal_document",
        "fields": [{"name": "jurisdiction", "type": "string"}],
        "search_queries": ["legal"],
        "buckets": [{"name": "national", "label": "National",
                     "field": "jurisdiction", "values": ["India"]}],
    }
    rows = [{"fields": {"jurisdiction": {"value": "India",
                                         "verification_status": "verified",
                                         "source": {}}}}]
    run_id = "run-compiled-buckets"
    main_mod.REPO.save_plan(plan_id, plan)
    main_mod.REPO.create_run(run_id, plan_id, plan)
    did = main_mod.REPO.finalize_dataset(run_id, plan, rows, {"records": 1})

    data = client.get(f"/api/datasets/{did}/buckets").json()["data"]

    views = {b["name"]: b for b in data["buckets"]}
    assert views["national"]["count"] == 1


def test_a_dataset_without_rules_says_so_instead_of_inventing_buckets(client):
    did = _dataset(buckets=[])
    data = client.get(f"/api/datasets/{did}/buckets").json()["data"]

    assert data["buckets"] == []
    assert "no buckets" in data["reason"]


def test_an_unknown_dataset_is_a_404(client):
    assert client.get("/api/datasets/not-a-dataset/buckets").status_code == 404
