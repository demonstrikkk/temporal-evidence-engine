"""The corpus import, against the store that actually matters.

The import was only ever exercised against `LocalRepo`, and `LocalRepo` stores
strings where Postgres stores uuid columns. So the import passed a label --
"legal_corpus" -- straight into `workflows.id` and it worked locally, and it
failed against the real database with:

    E_VALIDATION: not a valid id (expected a UUID):
    invalid input syntax for type uuid: "legal_corpus"

A test that only ever ran against the adapter that cannot fail this way is a
test that cannot see this. These run against a real Postgres and skip without
one.

They also check the thing the import exists to guarantee: that a value's
quote resolves against the *stored* text, read back out of the database rather
than from the object the import happened to be holding.
"""
from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.corpus_import import import_corpus

pytestmark = pytest.mark.postgres


def _repo():
    if not settings.DATABASE_URL:
        pytest.skip("no DATABASE_URL; the import cannot be checked without one")
    from app.repositories.postgres_repo import PostgresRepo
    return PostgresRepo(settings.DATABASE_URL)


@pytest.fixture()
def repo():
    """A real repository, and a store that is left as it was found.

    These tests write to the database the developer actually uses, so they
    clean up the datasets they create. A test that leaves a "postgres corpus
    probe" dataset in the Studio's dataset list on every run is noise the
    next person has to work out the origin of.
    """
    r = _repo()
    made: list[str] = []
    yield r, made
    for dataset_id in made:
        try:
            r.delete_dataset(dataset_id)
        except Exception:  # noqa: BLE001 - cleanup must not fail the test
            pass


def _corpus() -> dict:
    return {
        "corpus_version": 1,
        "edition": "test_domain",
        "edition_version": "1",
        "entity": "document",
        "name": "postgres corpus probe",
        "schema": [
            {"name": "document_title", "type": "string"},
            {"name": "tier", "type": "string"},
        ],
        "buckets": [
            {"name": "national", "label": "National", "field": "tier",
             "values": ["national"]},
        ],
        "documents": [{
            "doc_id": "probe-1",
            "title": "Probe document",
            "source_url": "https://example.test/probe-1",
            "text": "Title: Probe document\nTier: national\n\n"
                    "A body long enough to be a document rather than a line.",
            "fields": {
                "document_title": {"value": "Probe document",
                                   "quote": "Title: Probe document"},
                "tier": {"value": "national", "quote": "Tier: national"},
            },
        }],
    }


def test_the_import_survives_the_uuid_column(repo):
    """The bug, pinned: a label is not a legal value for `workflows.id`."""
    store, made = repo
    out = import_corpus(store, _corpus(), workflow_id="corpus_probe")
    made.append(out["dataset_id"])

    run = store.get_run(out["run_id"])
    assert run is not None
    assert run["workflow_id"], "the run must name the workflow it came from"

    # The plan is where the buckets rule lives, and the route looks it up
    # under the run's workflow id.
    plan = store.get_plan(str(run["workflow_id"]))
    assert plan, "the plan must be reachable under the workflow id"
    assert plan["buckets"]


def test_every_stored_quote_resolves_against_the_stored_page(repo):
    """A quote that does not slice back is not evidence, wherever it was read.

    Read back out of the database, not from the import's own memory: the
    offsets are only meaningful against the text the store holds.
    """
    store, made = repo
    out = import_corpus(store, _corpus(), workflow_id="corpus_probe")
    made.append(out["dataset_id"])

    dataset = store.get_dataset(out["dataset_id"])
    assert dataset is not None
    checked = 0
    for row in dataset["records"]:
        for name, cell in row["fields"].items():
            assert cell["verification_status"] == "verified", (
                f"{name} was not verified")
            source = cell["source"]
            page = store.get_page(str(source["page_id"]))
            assert page is not None
            assert page["markdown"][source["start"]:source["end"]] == source["quote"], (
                f"{name} does not resolve against its stored page")
            checked += 1
    assert checked == 2
