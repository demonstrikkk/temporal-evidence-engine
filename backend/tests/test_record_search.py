"""Full-text search reads the pages a record cites, and only those.

A judgment's fields are a title, a citation and a summary. The passage that
makes the case worth reading is in the body, on the page the cell cites. So a
search over the record's own fields cannot find it, and the answer is to join
through the citation.

The join is per record: a hit means *this record's own evidence* contains the
text. A page that merely exists in the same dataset, cited by nobody, must not
make every record match.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.repositories.local_repo import LocalRepo

BODY = "The arrest was arbitrary and the detention illegal. The appeal is allowed."


def _seed(repo):
    page_id = repo.upsert_page("run-1", {
        "url": "https://example.test/judgment",
        "content_hash": "hash-1",
        "markdown": BODY,
    })
    did = repo.finalize_dataset(
        "run-1",
        {"goal": "g", "fields": [{"name": "title"}]},
        [{"fields": {"title": {"value": "A v. B",
                               "source": {"page_id": page_id, "quote": "A v. B"}}}}],
        {"records": 1},
    )
    return did, page_id


def test_a_phrase_in_the_cited_page_is_found(tmp_path):
    repo = LocalRepo(str(tmp_path))
    did, _ = _seed(repo)

    out = repo.search_records(did, "arbitrary")

    assert out["total"] == 1
    assert out["matched_in"] == "stored page text"
    assert out["records"][0]["fields"]["title"]["value"] == "A v. B"


def test_search_is_case_insensitive(tmp_path):
    repo = LocalRepo(str(tmp_path))
    did, _ = _seed(repo)

    assert repo.search_records(did, "ARBITRARY")["total"] == 1


def test_a_page_the_record_does_not_cite_is_not_a_match(tmp_path):
    repo = LocalRepo(str(tmp_path))
    did, _ = _seed(repo)
    repo.upsert_page("run-1", {
        "url": "https://example.test/other",
        "content_hash": "hash-2",
        "markdown": "phenomenal",
    })

    assert repo.search_records(did, "phenomenal")["total"] == 0


def test_an_empty_query_is_no_search_rather_than_every_record(tmp_path):
    repo = LocalRepo(str(tmp_path))
    did, _ = _seed(repo)

    out = repo.search_records(did, "   ")

    assert out["total"] == 0
    assert out["records"] == []
    assert out["matched_in"] == "none"


def test_a_query_that_matches_nothing_returns_no_records(tmp_path):
    repo = LocalRepo(str(tmp_path))
    did, _ = _seed(repo)

    assert repo.search_records(did, "no-such-phrase-xyz")["total"] == 0
