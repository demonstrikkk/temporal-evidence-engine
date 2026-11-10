"""The temporal phase, end to end through the parts that persist and verify.

`test_temporal.py` pins the parser. This file pins the wiring: the plan carries
the spec, the crawler carries the page's date, the cell carries both, the
repository stores them, and none of it can change a verification status.
"""
import asyncio
from pathlib import Path

from app.repositories.local_repo import LocalRepo
from app.schemas.plan import TemporalSpec
from app.services import crawler as crawler_svc
from app.services import provenance as provenance_svc
from app.services import temporal
from app.services import validator as validator_svc
from app.services.planner import _finalize, _rule_plan


def run(coro):
    return asyncio.run(coro)


async def _noop(_):
    pass


# -- planner ------------------------------------------------------------------

def test_the_rule_based_plan_carries_the_requested_window():
    plan = _finalize(_rule_plan("funding between 2020 and 2023"), "funding between 2020 and 2023")
    assert plan["temporal"]["kind"] == "window"
    assert plan["temporal"]["start"] == "2020-01-01"
    assert plan["temporal"]["end"] == "2023-12-31"


def test_a_prompt_with_no_time_leaves_an_empty_spec():
    plan = _finalize(_rule_plan("top 10 banks"), "top 10 banks")
    assert plan["temporal"]["kind"] == "none"


def test_a_model_supplied_spec_survives_when_the_prompt_names_no_time():
    # The model is allowed to infer a date from context; the deterministic
    # parser only replaces it when the prompt itself said something explicit.
    supplied = TemporalSpec(kind="point", as_of="2024-09-29", source="llm").model_dump()
    plan = _finalize(_rule_plan("top 10 banks"), "top 10 banks")
    plan["temporal"] = supplied
    out = _finalize(plan, "top 10 banks")
    assert out["temporal"]["as_of"] == "2024-09-29"
    assert out["temporal"]["source"] == "llm"


def test_an_explicit_prompt_date_beats_a_model_supplied_one():
    supplied = TemporalSpec(kind="point", as_of="1999-01-01", source="llm").model_dump()
    plan = _finalize(_rule_plan("holdings as of 2024-09-29"),
                     "holdings as of 2024-09-29")
    plan["temporal"] = supplied
    out = _finalize(plan, "holdings as of 2024-09-29")
    assert out["temporal"]["as_of"] == "2024-09-29"
    assert out["temporal"]["source"] == "prompt"


def test_a_malformed_model_spec_degrades_to_none_instead_of_failing():
    plan = _finalize(_rule_plan("top 10 banks"), "top 10 banks")
    plan["temporal"] = {"kind": "not-a-kind", "as_of": ["not", "a", "string"]}
    out = _finalize(plan, "top 10 banks")
    assert out["temporal"]["kind"] == "none"


# -- validator ----------------------------------------------------------------

TEXT = "Acme Corp was founded in 2011 and employs roughly 240 people."
FIELDS = [{"name": "headcount", "type": "number", "required": True}]
RAW = {"fields": {"headcount": 240},
       "evidence": [{"field": "headcount", "value": 240,
                     "quote": "Acme Corp was founded in 2011 and employs roughly 240 people."}]}


def _wrap(**kwargs):
    return run(validator_svc.wrap_record(
        FIELDS, RAW, TEXT, "https://x.example/a", "Acme", **kwargs))


def test_a_cell_carries_the_page_date_and_the_check():
    spec = temporal.parse("between 2000 and 2015").model_dump()
    cell = _wrap(valid_time="2011-01-01", temporal=spec)["headcount"]
    assert cell["valid_time"] == "2011-01-01"
    assert cell["temporal_check"]["status"] == "within"


def test_a_wrong_year_does_not_demote_a_verifiable_quote():
    """The quote locates or it does not. A date mismatch is a relevance finding
    and must not touch `verification_status` — `settle` is the only chokepoint."""
    spec = temporal.parse("between 2020 and 2023").model_dump()
    cell = _wrap(valid_time="2011-01-01", temporal=spec)["headcount"]
    assert cell["verification_status"] == "verified"
    assert cell["temporal_check"]["status"] == "before"


def test_a_missing_page_date_is_unknown_not_outside():
    spec = temporal.parse("between 2020 and 2023").model_dump()
    cell = _wrap(temporal=spec)["headcount"]
    assert cell["temporal_check"]["status"] == "unknown"
    assert cell["verification_status"] == "verified"


def test_no_requested_time_means_no_check_at_all():
    cell = _wrap(valid_time="2011-01-01")["headcount"]
    assert cell["valid_time"] == "2011-01-01"
    assert cell["temporal_check"] == {}


def test_the_record_can_supply_the_date_when_the_caller_does_not():
    raw = {**RAW, "published_date": "2011-06-01T00:00:00Z"}
    wrapped = run(validator_svc.wrap_record(
        FIELDS, raw, TEXT, "https://x.example/a", "Acme",
        temporal=temporal.parse("as of 2012-01-01").model_dump()))
    assert wrapped["headcount"]["valid_time"] == "2011-06-01"
    # A datetime is a date for comparison purposes.
    assert wrapped["headcount"]["temporal_check"]["status"] == "within"


def test_a_provider_rfc_date_is_normalised_before_storage():
    # Tavily returns RFC 2822 dates for some sources; storing that verbatim
    # makes every reader re-parse it and the UI show "Tue, 11 Ma".
    raw = {**RAW, "published_date": "Tue, 11 Mar 2025 17:00:00 GMT"}
    wrapped = run(validator_svc.wrap_record(
        FIELDS, raw, TEXT, "https://x.example/a", "Acme"))
    assert wrapped["headcount"]["valid_time"] == "2025-03-11"


# -- crawler ------------------------------------------------------------------

HTML = "<html><body><h1>Acme Corp</h1><p>Acme employs 240 people.</p></body></html>"


def test_the_search_results_publication_date_reaches_the_page():
    stored = {}

    async def _fetch(url, method):
        return {"url": url, "final_url": url, "title": "Acme",
                "html": HTML, "method": "http"}

    async def _persist(page):
        stored[page["url"]] = page
        return "page-1"

    pages, _ = run(crawler_svc.fetch_all(
        [{"url": "https://x.example/a", "title": "",
          "published_date": "2011-06-01T00:00:00Z"}],
        _fetch, _noop, _noop, {}, _persist))
    assert pages[0]["published_date"] == "2011-06-01T00:00:00Z"
    assert stored["https://x.example/a"]["published_date"] == "2011-06-01T00:00:00Z"


def test_a_reused_page_keeps_its_stored_date():
    async def _fetch(url, method):
        raise AssertionError("reuse must not fetch")

    async def _reuse(url):
        return {"id": "p-old", "url": url, "title": "Acme", "markdown": "Acme employs 240 people.",
                "published_date": "2021-03-04", "content_hash": "h", "method": "crawl4ai"}

    pages, _ = run(crawler_svc.fetch_all(
        [{"url": "https://x.example/a", "title": ""}],
        _fetch, _noop, _noop, {}, None, _reuse))
    assert pages[0]["published_date"] == "2021-03-04"


# -- persistence --------------------------------------------------------------

def test_the_dataset_remembers_the_window_it_asked_for(tmp_path: Path):
    repo = LocalRepo(str(tmp_path / "store"))
    plan = {"goal": "funding", "fields": [{"name": "name", "type": "string"}],
            "temporal": TemporalSpec(kind="window", start="2020-01-01",
                                     end="2023-12-31").model_dump()}
    did = repo.finalize_dataset("run-1", plan, [], {})
    got = repo.get_dataset(did)
    assert got["temporal"]["start"] == "2020-01-01"
    assert got["temporal"]["end"] == "2023-12-31"
    assert repo.list_datasets()[0]["temporal"]["kind"] == "window"


def test_the_dataset_row_variant_carries_the_window_too(tmp_path: Path):
    # `/api/datasets/{id}` reads this variant, so omitting it here would make
    # the detail page disagree with the list about the same dataset.
    repo = LocalRepo(str(tmp_path / "store"))
    plan = {"goal": "funding", "fields": [],
            "temporal": TemporalSpec(kind="point", as_of="2024-09-29").model_dump()}
    did = repo.finalize_dataset("run-1", plan, [], {})
    assert repo.get_dataset_row(did)["temporal"]["as_of"] == "2024-09-29"


def test_a_stored_page_keeps_its_publication_date(tmp_path: Path):
    repo = LocalRepo(str(tmp_path / "store"))
    pid = repo.upsert_page("run-1", {"url": "https://x.example/a", "title": "Acme",
                                     "markdown": "Acme employs 240 people.",
                                     "content_hash": "h", "published_date": "2021-03-04"})
    got = repo.find_page_by_url("https://x.example/a")
    assert got["id"] == pid
    assert got["published_date"] == "2021-03-04"


# -- run summary ---------------------------------------------------------------

def test_the_run_summary_counts_dated_cells_and_window_mismatches():
    """A live run has to be able to say "3 sources are outside the window".

    Without this the only reader of `temporal_check` was the per-cell tooltip,
    so a run could be entirely out of period and still report a clean summary.
    """
    spec = temporal.parse("between 2020 and 2023").model_dump()
    rows = [
        {"fields": {"a": _wrap(valid_time="2021-01-01", temporal=spec)["headcount"]}},
        {"fields": {"b": _wrap(valid_time="2011-01-01", temporal=spec)["headcount"]}},
        {"fields": {"c": _wrap(temporal=spec)["headcount"]}},
    ]
    summary = provenance_svc.summarize(rows)
    assert summary["cells_dated"] == 2
    assert summary["temporal_within"] == 1
    assert summary["temporal_before"] == 1
    assert summary["temporal_after"] == 0
    assert summary["temporal_unknown"] == 1
    # A date finding never moves a cell out of `verified`.
    assert summary["fields_verified"] == 3


def test_an_undated_cell_is_not_reported_as_outside():
    spec = temporal.parse("as of 2024-09-29").model_dump()
    rows = [{"fields": {"a": _wrap(temporal=spec)["headcount"]}}]
    summary = provenance_svc.summarize(rows)
    assert summary["temporal_before"] == 0
    assert summary["temporal_after"] == 0
    assert summary["temporal_unknown"] == 1


def test_every_temporal_counter_is_declared_on_the_run_schema():
    # The handler enumerates counter keys by hand; pydantic drops anything the
    # model does not declare, which is how two verification counters were
    # computed and lost on every run.
    from app.schemas.run import Counters

    for key in ("cells_dated", "temporal_within", "temporal_before",
                "temporal_after", "temporal_uncertain", "temporal_unknown"):
        assert key in Counters.model_fields


def test_migration_013_is_additive_and_has_defaults():
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "013_temporal.sql").read_text(
        encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS" in sql
    assert "datasets" in sql and "temporal_json" in sql
    assert "pages" in sql and "published_date" in sql
    # Every new column needs a default so an old writer keeps working.
    assert sql.count("DEFAULT") >= 2
