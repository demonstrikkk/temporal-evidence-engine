"""The scorecard's citation report: does the stored evidence still resolve?

`quality` has its own unit tests. This pins the wiring the scorecard adds —
finding the page a cell cites, reading its stored markdown, and reporting a
missing page as a defect rather than excusing it.
"""
import importlib.util
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[1] / "scripts" / "coverage_scorecard.py")


def _load():
    spec = importlib.util.spec_from_file_location("coverage_scorecard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scorecard = _load()


class FakeRepo:
    def __init__(self, records, pages):
        self.records = records
        self.pages = pages
        self.asked: list[list[str]] = []

    def get_records(self, dataset_id, q="", limit=100, offset=0):
        return {"records": self.records[offset:offset + limit], "total": len(self.records)}

    def get_pages_by_ids(self, page_ids):
        self.asked.append(list(page_ids))
        wanted = set(page_ids)
        return [p for p in self.pages if p["id"] in wanted]

    def get_dataset_row(self, dataset_id):
        return {"id": dataset_id, "run_id": "run-1", "schema": [],
                "temporal": {"kind": "window", "start": "2020-01-01", "end": "2023-12-31"}}


def _cell(value, quote, page_id, valid_time="", status=""):
    return {"value": value,
            "verification_status": "verified",
            "source": {"quote": quote, "page_id": page_id},
            "valid_time": valid_time,
            "temporal_check": {"status": status} if status else {}}


def test_a_cell_resolves_against_the_page_it_cites():
    repo = FakeRepo(
        records=[{"fields": {"name": _cell("Acme", "Acme Corp", "p1", "2021-01-01", "within")}}],
        pages=[{"id": "p1", "markdown": "Acme Corp employs 40 people."}])
    report = scorecard._report(repo, "d1")
    assert report["citations"]["exact"] == 1
    assert report["citations"]["resolution_rate"] == 1.0
    assert report["citations"]["pages_cited"] == 1
    assert report["citations"]["pages_found"] == 1
    assert report["citations"]["cells_dated"] == 1
    assert report["citations"]["temporal_statuses"] == {"within": 1}


def test_a_cited_page_that_is_gone_is_no_text_not_a_pass():
    # The page id is real but the store cannot produce it; the citation is
    # unverifiable, which is the finding, not an absence to skip.
    repo = FakeRepo(
        records=[{"fields": {"name": _cell("Acme", "Acme Corp", "p-gone")}}],
        pages=[])
    report = scorecard._report(repo, "d1")
    assert report["citations"]["no_text"] == 1
    assert report["citations"]["resolution_rate"] == 0.0


def test_a_wrong_quote_is_reported_as_unlocatable():
    repo = FakeRepo(
        records=[{"fields": {"name": _cell("Beta", "a sentence not on the page", "p1")}}],
        pages=[{"id": "p1", "markdown": "Acme Corp employs 40 people."}])
    report = scorecard._report(repo, "d1")
    assert report["citations"]["unlocatable"] == 1


def test_the_report_carries_the_requested_window():
    repo = FakeRepo(records=[], pages=[])
    report = scorecard._report(repo, "d1")
    assert report["temporal"]["start"] == "2020-01-01"
    assert report["citations"]["total"] == 0


def test_the_report_counts_how_each_value_was_read():
    """The ladder's claim is measurable: how much was never sent to a model."""
    def cell(value, method):
        return {"value": value, "verification_status": "verified",
                "read_method": method,
                "source": {"quote": value, "page_id": "p1"}}

    repo = FakeRepo(
        records=[{"fields": {"company_name": cell("Acme", "json_ld"),
                             "industry": cell("AI", "labelled"),
                             "ceo": cell("Jane", "llm"),
                             "empty": cell("", "llm")}}],
        pages=[{"id": "p1", "markdown": "Acme AI Jane"}])
    report = scorecard._report(repo, "d1")
    assert report["citations"]["read_methods"] == {
        "json_ld": 1, "labelled": 1, "llm": 1}
    # An empty cell is not a value, so it must not pad a route's count.
    assert sum(report["citations"]["read_methods"].values()) == 3
