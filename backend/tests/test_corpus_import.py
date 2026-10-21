"""A curated corpus enters through the same evidence gates as a live run."""
from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.repositories.local_repo import LocalRepo
from app.services.buckets import derive
from app.services.corpus_import import import_corpus


def _document(doc_id: str, court: str, jurisdiction: str, year: int,
              body: str) -> dict:
    text = (f"Title: Document {doc_id}\nCourt: {court}\n"
            f"Jurisdiction: {jurisdiction}\nYear: {year}\n\n{body}")
    return {
        "doc_id": doc_id,
        "title": f"Document {doc_id}",
        "source_url": f"https://example.test/{doc_id}",
        "fetch_url": f"https://example.test/{doc_id}.pdf",
        "content_type": "application/pdf",
        "text": text,
        "fields": {
            "document_title": {"value": f"Document {doc_id}",
                               "quote": f"Title: Document {doc_id}"},
            "court": {"value": court, "quote": f"Court: {court}"},
            "jurisdiction": {"value": jurisdiction,
                             "quote": f"Jurisdiction: {jurisdiction}"},
            "year": {"value": year, "quote": f"Year: {year}"},
            "summary": {"value": body, "quote": body},
        },
    }


def _corpus() -> dict:
    return {
        "corpus_version": 1,
        "edition": "test_legal",
        "edition_version": "1",
        "entity": "legal_document",
        "name": "test corpus",
        "schema": [
            {"name": "document_title", "type": "string"},
            {"name": "court", "type": "string"},
            {"name": "jurisdiction", "type": "string"},
            {"name": "year", "type": "number"},
            {"name": "summary", "type": "string"},
        ],
        "buckets": [
            {"name": "regional", "label": "Regional", "field": "jurisdiction",
             "values": ["Delhi"]},
            {"name": "national", "label": "National", "field": "jurisdiction",
             "values": ["India"]},
        ],
        "documents": [
            _document("a", "Supreme Court of India", "India", 2024,
                      "Free legal aid is a fundamental right."),
            _document("b", "High Court of Delhi", "Delhi", 2025,
                      "The High Court considered the request for legal aid."),
        ],
    }


def test_import_stores_pages_quotes_offsets_and_a_dataset(tmp_path) -> None:
    repo = LocalRepo(str(tmp_path / "store"))
    out = import_corpus(repo, _corpus(), workflow_id="legal_corpus")

    assert out["records"] == 2
    assert out["fields_verified"] == 10
    assert out["fields_unverified"] == 0

    dataset = repo.get_dataset(out["dataset_id"])
    assert len(dataset["records"]) == 2
    first = dataset["records"][0]["fields"]
    for cell in first.values():
        assert cell["verification_status"] == "verified"
        assert cell["source"]["page_id"]
        assert cell["source"]["start"] is not None
        assert cell["source"]["end"] is not None
        page = repo.get_page(cell["source"]["page_id"])
        assert cell["source"]["quote"] == page["markdown"][
            cell["source"]["start"]:cell["source"]["end"]]

    run = repo.get_run(out["run_id"])
    assert run["status"] == "COMPLETED"
    assert run["progress"] == 100


def test_import_keeps_a_value_the_quote_does_not_support_unverified(tmp_path) -> None:
    payload = _corpus()
    payload["documents"][0]["fields"]["court"] = {
        "value": "Somewhere Else", "quote": "Court: Supreme Court of India"}

    repo = LocalRepo(str(tmp_path / "store"))
    out = import_corpus(repo, payload)
    cell = repo.get_dataset(out["dataset_id"])["records"][0]["fields"]["court"]

    assert cell["verification_status"] == "unverified"
    assert "does not contain" in cell["not_verified_because"]


def test_a_quote_that_does_not_resolve_is_refused_before_anything_is_written(
        tmp_path) -> None:
    payload = _corpus()
    payload["documents"][0]["fields"]["summary"]["quote"] = "not on the page"

    repo = LocalRepo(str(tmp_path / "store"))
    with pytest.raises(AppError) as err:
        import_corpus(repo, payload)

    assert err.value.code == "E_VALIDATION"
    assert repo.run_history() == []


def test_duplicate_document_ids_are_refused(tmp_path) -> None:
    payload = _corpus()
    payload["documents"][1]["doc_id"] = payload["documents"][0]["doc_id"]

    repo = LocalRepo(str(tmp_path / "store"))
    with pytest.raises(AppError) as err:
        import_corpus(repo, payload)

    assert "duplicate doc_id" in err.value.message


def test_imported_plan_keeps_the_edition_bucket_rules(tmp_path) -> None:
    repo = LocalRepo(str(tmp_path / "store"))
    out = import_corpus(repo, _corpus())
    plan = repo.get_plan(out["run_id"])
    records = repo.get_records(out["dataset_id"], "", 100, 0)["records"]

    views = {b["name"]: b for b in derive(plan["buckets"], records)["buckets"]}
    assert views["national"]["count"] == 1
    assert views["regional"]["count"] == 1
    assert views["regional"]["record_ids"] == ["local:1"]
    assert plan["edition"] == "test_legal"
