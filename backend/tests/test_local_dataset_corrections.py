"""A correction in the JSONL store is a whole new dataset row.

That makes the writer responsible for restating every column: whatever it
leaves out is gone, because readers take the newest line. The live failure this
guards against — a cell correction dropping `temporal` and `edition` so the
dataset page showed no window — happened with real data, and the same append
also made `/api/datasets` list one dataset twice.

Hermetic: a temporary directory and the local adapter, no network, no database.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.repositories.local_repo import LocalRepo  # noqa: E402

PLAN = {"goal": "London AI funding", "fields": [{"name": "company_name"}],
        "edition": "generic",
        "temporal": {"kind": "window", "start": "2024-01-01",
                     "end": "2025-12-31", "source": "prompt"}}
COUNTS = {"records": 1, "fields_verified": 1, "cells_dated": 1}


def _seed(repo: LocalRepo) -> str:
    return repo.finalize_dataset(
        "run-1", PLAN,
        [{"fields": {"company_name": {"value": "Acme",
                                      "verification_status": "unverified"}}}],
        dict(COUNTS))


def test_a_cell_correction_keeps_the_dataset_columns(tmp_path):
    repo = LocalRepo(str(tmp_path))
    did = _seed(repo)
    created = repo.get_dataset_row(did)["created_at"]

    assert repo.update_record_cell(
        did, f"{did}:0", "company_name",
        {"value": "Acme Ltd", "verification_status": "verified"}) is True

    row = repo.get_dataset_row(did)
    assert row["temporal"]["start"] == "2024-01-01"
    assert row["temporal"]["end"] == "2025-12-31"
    assert row["edition"] == "generic"
    assert row["counts"]["fields_verified"] == 1
    assert row["created_at"] == created
    # The correction itself landed.
    assert repo.get_dataset(did)["records"][0]["fields"]["company_name"]["value"] \
        == "Acme Ltd"


def test_a_schema_correction_keeps_the_dataset_columns(tmp_path):
    repo = LocalRepo(str(tmp_path))
    did = _seed(repo)

    assert repo.update_dataset_schema(
        did, [{"name": "company_name"}, {"name": "website"}]) is True

    row = repo.get_dataset_row(did)
    assert [f["name"] for f in row["schema"]] == ["company_name", "website"]
    assert row["temporal"]["kind"] == "window"
    assert row["edition"] == "generic"
    assert row["counts"]["records"] == 1


def test_a_corrected_dataset_is_listed_once_with_its_counts(tmp_path):
    repo = LocalRepo(str(tmp_path))
    did = _seed(repo)
    repo.update_record_cell(
        did, f"{did}:0", "company_name",
        {"value": "Acme Ltd", "verification_status": "verified"})

    listed = repo.list_datasets()
    assert [d["id"] for d in listed] == [did], "one dataset, one row"
    assert listed[0]["temporal"]["kind"] == "window"
    assert listed[0]["counts"]["cells_dated"] == 1
    assert listed[0]["record_count"] == 1
