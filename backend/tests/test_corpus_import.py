"""A curated corpus enters through the same evidence gates as a live run.

The corpus here is deliberately generic. These are the core's tests, and the
core must not know what a legal document is -- the words that make a boolean
true travel with the corpus file, so the importer only checks that a declared
term is present.
"""
from __future__ import annotations

import uuid

import pytest

from app.core.errors import AppError
from app.repositories.local_repo import LocalRepo
from app.services.buckets import derive
from app.services.corpus_import import import_corpus


def _document(doc_id: str, tier: str, year: int, body: str,
              **extra: dict) -> dict:
    text = (f"Title: Document {doc_id}\nTier: {tier}\n"
            f"Year: {year}\n\n{body}")
    fields = {
        "document_title": {"value": f"Document {doc_id}",
                           "quote": f"Title: Document {doc_id}"},
        "tier": {"value": tier, "quote": f"Tier: {tier}"},
        "year": {"value": year, "quote": f"Year: {year}"},
        "summary": {"value": body, "quote": body},
    }
    fields.update(extra)
    return {
        "doc_id": doc_id,
        "title": f"Document {doc_id}",
        "source_url": f"https://example.test/{doc_id}",
        "fetch_url": f"https://example.test/{doc_id}.pdf",
        "content_type": "application/pdf",
        "text": text,
        "fields": fields,
    }


def _corpus() -> dict:
    return {
        "corpus_version": 1,
        "edition": "test_domain",
        "edition_version": "1",
        "entity": "document",
        "name": "test corpus",
        "schema": [
            {"name": "document_title", "type": "string"},
            {"name": "tier", "type": "string"},
            {"name": "year", "type": "number"},
            {"name": "summary", "type": "string"},
            {"name": "eligible", "type": "boolean"},
        ],
        "buckets": [
            {"name": "regional", "label": "Regional", "field": "tier",
             "values": ["regional"]},
            {"name": "national", "label": "National", "field": "tier",
             "values": ["national"]},
        ],
        "documents": [
            _document("a", "national", 2024, "Every person is entitled to a hearing."),
            _document("b", "regional", 2025,
                      "Every person is entitled to a hearing.",
                      eligible={"value": True,
                                "quote": "Every person is entitled to a hearing.",
                                "support_terms": ["entitled"]}),
        ],
    }


def test_import_stores_pages_quotes_offsets_and_a_dataset(tmp_path) -> None:
    repo = LocalRepo(str(tmp_path / "store"))
    out = import_corpus(repo, _corpus(), workflow_id="domain_corpus")

    assert out["records"] == 2
    assert out["fields_verified"] == 9
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


def test_a_boolean_is_supported_by_the_terms_the_corpus_declares(tmp_path) -> None:
    """The words that make a boolean true come from the corpus, not the core.

    The core once hardcoded one vertical's words here. It now reads the terms
    the field declares, and a term that is not in the quote is not support.
    """
    payload = _corpus()
    payload["documents"][1]["fields"]["eligible"] = {
        "value": True, "quote": "Every person is entitled to a hearing.",
        "support_terms": ["qualifies"],
    }

    repo = LocalRepo(str(tmp_path / "store"))
    out = import_corpus(repo, payload)
    cell = repo.get_dataset(out["dataset_id"])["records"][1]["fields"]["eligible"]

    assert cell["verification_status"] == "unverified"
    assert "does not contain" in cell["not_verified_because"]


def test_a_boolean_with_no_declared_terms_is_not_supported(tmp_path) -> None:
    payload = _corpus()
    payload["documents"][1]["fields"]["eligible"] = {
        "value": True, "quote": "Every person is entitled to a hearing.",
    }

    repo = LocalRepo(str(tmp_path / "store"))
    out = import_corpus(repo, payload)
    cell = repo.get_dataset(out["dataset_id"])["records"][1]["fields"]["eligible"]

    assert cell["verification_status"] == "unverified"


def test_an_imported_cell_carries_the_documents_own_year(tmp_path) -> None:
    """A corpus document dates itself; its cells must keep that date.

    Without this every imported cell was undated, so a temporal request over the
    corpus had nothing to compare against even where the document's own metadata
    plainly said the year.
    """
    repo = LocalRepo(str(tmp_path / "store"))
    out = import_corpus(repo, _corpus())
    records = repo.get_records(out["dataset_id"], "", 100, 0)["records"]

    for record in records:
        year = record["fields"]["year"]["value"]
        assert record["fields"]["summary"]["valid_time"] == str(year)
        page = repo.get_page(record["fields"]["summary"]["source"]["page_id"])
        assert page["published_date"] == str(year)


def test_import_keeps_a_value_the_quote_does_not_support_unverified(tmp_path) -> None:
    payload = _corpus()
    payload["documents"][0]["fields"]["tier"] = {
        "value": "somewhere-else", "quote": "Tier: national"}

    repo = LocalRepo(str(tmp_path / "store"))
    out = import_corpus(repo, payload)
    cell = repo.get_dataset(out["dataset_id"])["records"][0]["fields"]["tier"]

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
    plan = _plan_of(repo, out["run_id"])
    records = repo.get_records(out["dataset_id"], "", 100, 0)["records"]

    views = {b["name"]: b for b in derive(plan["buckets"], records)["buckets"]}
    assert views["national"]["count"] == 1
    assert views["regional"]["count"] == 1
    assert views["regional"]["record_ids"] == ["local:1"]
    assert plan["edition"] == "test_domain"


def _plan_of(repo, run_id: str) -> dict:
    """The plan a run was built from, however that store keeps it.

    A compiled run's plan lives under the plan id and the run points at it;
    the import stores it the same way.
    """
    plan = repo.get_plan(run_id)
    if plan:
        return plan
    run = repo.get_run(run_id) or {}
    return repo.get_plan(str(run.get("workflow_id") or "")) or {}


def test_the_workflow_id_is_a_uuid_because_the_database_column_is_one(
        tmp_path) -> None:
    """`workflows.id` is a uuid column, so a label is not a legal value for it.

    The import passed "legal_corpus" straight through. `LocalRepo` stores a
    string and accepted it, so the import worked locally and failed against
    Postgres with "invalid input syntax for type uuid" -- a bug that only
    existed in the store that matters.
    """
    repo = LocalRepo(str(tmp_path / "store"))
    out = import_corpus(repo, _corpus(), workflow_id="legal_corpus")

    run = repo.get_run(out["run_id"]) or {}
    workflow_id = str(run.get("workflow_id") or "")

    assert workflow_id, "the run must name the workflow it came from"
    uuid.UUID(workflow_id)  # raises if it is not a uuid

    # And the plan is reachable under that id, which is where the buckets
    # route looks for it.
    assert repo.get_plan(workflow_id)["edition"] == "test_domain"


def test_the_same_label_maps_to_the_same_workflow(tmp_path) -> None:
    """Re-importing a corpus must not leave a new workflow behind each time.

    The id is derived from the label, so the second import updates the same
    workflow row instead of adding another one.
    """
    repo = LocalRepo(str(tmp_path / "store"))
    first = import_corpus(repo, _corpus(), workflow_id="legal_corpus")
    second = import_corpus(repo, _corpus(), workflow_id="legal_corpus")

    one = (repo.get_run(first["run_id"]) or {}).get("workflow_id")
    two = (repo.get_run(second["run_id"]) or {}).get("workflow_id")

    assert one == two
    # Two runs, two datasets: each import is its own run.
    assert first["run_id"] != second["run_id"]
    assert first["dataset_id"] != second["dataset_id"]
