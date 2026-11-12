"""Guards for the independent assertion-coverage evaluation.

The suite is a measurement, not a product feature. These tests keep it honest in
four ways:

* the ground truth must resolve and must not acquire scanner-shaped fields;
* the runner must stay offline and deterministic;
* the measured numbers must not drift without the report changing;
* the production numbers and gold files the suite compares against must not
  drift either.

Run from the repository root, or let the backend gate collect it:

    backend\\.venv\\Scripts\\python.exe -m pytest backend\\tests\\test_independent_coverage_eval.py -q
"""
from __future__ import annotations

import ast
import functools
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
EVAL = ROOT / "eval" / "independent"
RUN = EVAL / "run.py"

# `import app` uses the same path the backend pytest config supplies; `import
# eval` needs the repository root because the suite is outside the package.
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT))

#: Normalised content hashes. CRLF is folded to LF so a checkout setting does
#: not look like a change. Updating any of these files is a deliberate decision
#: that must also update the report and this table.
PINNED_FILES = {
    "eval/gold.jsonl":
        "8e05bf128624eedfb6a5c455849c16f76fef58ab4c4777c3d8cdf1c96c66435e",
    "eval/gold_generated.jsonl":
        "f86ff70155cc93e0a509c089fcfbe2f8048f43058d9726e9f9fe3d81c5a66a20",
    "eval/gold_live.jsonl":
        "226abfee9177fabad89d4a0dc3f4c41b2aea7a23cb3c759d667f9ca652c40ba0",
    "eval/assertion_gold.jsonl":
        "51d0cafe1b562fc704062bf4b2f55b41a93b4ac593d77e010ec7994339383b83",
    "eval/coverage_taxonomy.jsonl":
        "c8a00cc702500f742a12825275f83011ed18c2c64a36d48eafadcb905e8a2ce6",
    "eval/run_eval.py":
        "ac453e84bc51629861811d6bf97d44adca41667bf5762ee6845838dd3e92c1b3",
    "eval/extraction_suite.py":
        "1d5a7acfbfae3ca04c6c46b095a89a3fa7e8ba196fd26418465a4d0c2ba56248",
    "eval/independent/v1/annotations.jsonl":
        "c7854823d5ab8209317a385e80b6dbc59d9180807e5b5771ba8c301d99c9bdca",
}

PRODUCTION_FIXTURES = (
    (
        "gold_eiffel_two_claims",
        "The Eiffel Tower is 330 metres tall. It was completed in 1889 for "
        "the World's Fair in Paris.",
        [{"id": "c1", "span_start": 0, "span_end": 40},
         {"id": "c2", "span_start": 41, "span_end": 72}],
        (2, 2, 2, 0, 0, 1.0, 1.0,
         {"checked": 2, "subjective": 0, "prediction": 0, "not_checked": 0}),
    ),
    (
        "uncovered_measure",
        "The tower is 330 metres tall.",
        [],
        (1, 0, 1, 0, 0, 0.0, 0.0,
         {"checked": 0, "subjective": 0, "prediction": 0, "not_checked": 1}),
    ),
    (
        "no_value_no_claim",
        "The tower is in Paris.",
        [],
        (0, 0, 0, 0, 1, 1.0, 1.0,
         {"checked": 0, "subjective": 0, "prediction": 0, "not_checked": 0}),
    ),
    (
        "unscanned_sentence",
        "The tower is 330 metres tall. It is in Paris.",
        [{"id": "c1", "span_start": 0, "span_end": 29}],
        (1, 1, 1, 0, 1, 1.0, 1.0,
         {"checked": 1, "subjective": 0, "prediction": 0, "not_checked": 0}),
    ),
)


@functools.lru_cache(maxsize=1)
def _runner():
    spec = importlib.util.spec_from_file_location("tev_independent_run", RUN)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves string annotations through sys.modules, so the
    # module has to be registered before its body executes.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def payload():
    return _runner().build_payload()


def _digest(path: Path) -> str:
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# the dataset is valid and independent
# ---------------------------------------------------------------------------

def test_dataset_version_and_every_span_resolve():
    run = _runner()
    cases = run.load_cases()          # raises ValueError on any bad span or field
    assert run.VERSION == "independent-1.0.0"
    assert len(cases) == 29
    assert all(case["dataset_version"] == run.VERSION for case in cases)


def test_dataset_covers_every_required_case_category():
    run = _runner()
    cases = run.load_cases()
    assert {case["category"] for case in cases} == set(run.REQUIRED_CASE_CATEGORIES)


def test_ground_truth_has_no_scanner_shaped_fields():
    cases = _runner().load_cases()
    forbidden = {"kind", "expected_disposition", "expected_match", "scanner_kind"}
    for case in cases:
        assert case["provenance"]["scanner_output_used"] is False
        assert case["provenance"]["independence"]
        for assertion in case["assertions"]:
            assert not (forbidden & set(assertion)), assertion["id"]
            assert assertion["rationale"].strip()
            assert assertion["normalized"].strip()
            assert assertion["subject"].strip()
            assert assertion["property"].strip()

    # A benchmark made only of values reproduces the circularity it exists to
    # escape. Prose-only assertions must be a substantial part of the set.
    value_less = [
        assertion
        for case in cases for assertion in case["assertions"]
        if assertion["value_text"] is None
    ]
    assert len(value_less) >= 10


def test_every_assertion_has_a_materiality_and_scope_disposition():
    cases = _runner().load_cases()
    for case in cases:
        for assertion in case["assertions"]:
            assert isinstance(assertion["material"], bool)
            assert isinstance(assertion["in_scope"], bool)
            assert assertion["assertion_type"] in {
                "factual", "subjective", "predictive", "ambiguous"}
            if not assertion["in_scope"]:
                assert assertion["assertion_type"] != "factual"


# ---------------------------------------------------------------------------
# the runner is offline, deterministic, and not wired into production
# ---------------------------------------------------------------------------

def test_runner_imports_no_network_or_model_client():
    tree = ast.parse(RUN.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)

    forbidden = (
        "app.bifrost", "app.research", "app.api", "httpx", "requests",
        "urllib", "http", "socket", "aiohttp", "openai", "anthropic",
        "tavily",
    )
    for module in modules:
        assert not any(module == name or module.startswith(name + ".")
                       for name in forbidden), module

    app_imports = {module for module in modules if module.startswith("app.")}
    assert app_imports <= {
        "app.core.config", "app.verify.assertions", "app.verify.coverage",
        "app.verify.segment",
    }


def test_runner_opens_no_socket(monkeypatch):
    import socket as socket_module

    def refuse(*_args, **_kwargs):
        raise AssertionError("the independent suite must not use the network")

    monkeypatch.setattr(socket_module, "socket", refuse)
    monkeypatch.setattr(socket_module, "create_connection", refuse)
    result = _runner().build_payload()
    assert result["cost"]["network_calls"] == 0


def test_runner_is_deterministic(payload):
    second = _runner().build_payload()
    first = json.loads(json.dumps(payload))     # do not mutate the fixture
    first["run"].pop("elapsed_ms")
    second["run"].pop("elapsed_ms")
    assert first == second


def test_production_does_not_import_the_new_suite():
    for path in (BACKEND / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not (name == "eval" or name.startswith("eval.")), path


# ---------------------------------------------------------------------------
# the measured numbers cannot drift silently
# ---------------------------------------------------------------------------

def test_overall_metrics_are_pinned(payload):
    overall = payload["overall"]
    assert overall["material_in_scope"] == 53
    assert overall["exact"] == 32
    assert overall["partial"] == 2
    assert overall["touched_only"] == 2
    assert overall["miss"] == 17
    assert overall["candidates"] == 36
    assert overall["exact_candidates"] == 32
    assert overall["partial_candidates"] == 3
    assert overall["false_candidates"] == 1
    assert overall["recall"] == 0.6038
    assert overall["partial_recall"] == 0.6415
    assert overall["touch_rate"] == 0.6792
    assert overall["precision"] == 0.8889
    assert overall["f1"] == 0.7191
    assert overall["omission_rate"] == 0.3962
    assert overall["false_candidate_rate"] == 0.0278
    assert overall["value_type_agreement"] == 1.0
    assert overall["false_excuses"] == 1


def test_sentence_and_compound_metrics_are_pinned(payload):
    sentences = payload["sentence_level"]
    assert sentences["sentences_with_material"] == 27
    assert sentences["fully_covered"] == 9
    assert sentences["touched"] == 23

    compound = payload["compound"]
    assert compound["sentences"] == 20
    assert compound["fully_covered"] == 5
    assert compound["value_sentences"] == 10
    assert compound["value_split_ok"] == 7


def test_category_tables_cover_every_declared_category(payload):
    run = _runner()
    by_case = payload["category_recall"]["by_case_category"]
    by_assertion = payload["category_recall"]["by_assertion_category"]
    assert set(by_case) == set(run.REQUIRED_CASE_CATEGORIES)
    assert set(by_assertion) == set(run.ASSERTION_CATEGORIES)
    for name in ("subjective", "predictions", "ambiguous"):
        assert by_case[name]["material_in_scope"] == 0
        assert by_case[name]["recall"] is None


def test_failure_taxonomy_counts_are_pinned(payload):
    counts = {name: row["count"]
              for name, row in payload["failure_taxonomy"].items()}
    assert counts == {
        "entity_recognition": 4,
        "materiality_classification": 1,
        "numeric_recognition": 4,
        "property_classification": 6,
        "relationship_extraction": 5,
        "version_vs_quantity": 1,
    }


def test_every_non_exact_assertion_is_inspectable(payload):
    assert len(payload["misses"]) == 21
    classes = set(payload["failure_taxonomy"])
    for row in payload["misses"]:
        assert row["failure_class"] in classes
        assert row["sentence"]
        assert row["span_start"] < row["span_end"]
        assert isinstance(row["candidates_seen"], list)
        if row["match"] == "partial":
            assert row["candidates_seen"]


def test_complete_coverage_while_blind_cases_are_pinned(payload):
    cases = [row["case"] for row in payload["complete_coverage_while_missed"]]
    assert cases == ["ind-07", "ind-09", "ind-21", "ind-24"]


def test_many_to_one_shared_value_is_credited_without_duplication(payload):
    run = _runner()
    case = next(item for item in payload["cases"] if item["id"] == "ind-29")
    prices = [row for row in case["assertions"] if row["text"] == "$99 per month"]
    assert len(prices) == 2
    assert all(row["match"] == "exact" for row in prices)
    assert all(row["shared_value"] for row in prices)
    assert payload["duplicates"]["shared_value_candidates"] == 1
    assert payload["duplicates"]["duplicate_rate"] == 0.0

    # The shared value still does not cover the entity assertion beside it.
    entity = next(row for row in case["assertions"]
                  if row["category"] == "entity")
    assert entity["match"] == "miss"
    assert entity["failure_class"] == "entity_recognition"
    assert run is not None


def test_d031_examples_are_measured_not_repaired(payload):
    observed = {row["text"]: row for row in payload["d031"]}
    assert observed["7 million"]["observed"] == "no candidate"
    assert observed["7 million"]["classification"] == "scale_word_unrecognized"
    assert observed["1.5 million"]["observed"] == "version: 1.5"
    assert observed["1.5 million"]["classification"] == "version_vs_quantity"
    assert observed["3 billion"]["observed"] == "no candidate"
    assert observed["10,100 tonnes"]["observed"] == "measure: 10,100 tonnes"
    assert observed["10,100 tonnes"]["classification"] == "recognized_measure"
    assert all(row["matches_documented"] for row in payload["d031"])


def test_cost_is_zero_and_latency_is_reported(payload):
    cost = payload["cost"]
    assert cost["model_calls"] == 0
    assert cost["tavily_calls"] == 0
    assert cost["network_calls"] == 0
    assert cost["database_queries"] == 0
    assert cost["estimated_usd"] == 0.0
    assert payload["run"]["elapsed_ms"] >= 0


# ---------------------------------------------------------------------------
# production invariants the suite compares against
# ---------------------------------------------------------------------------

def test_production_rule_version_is_pinned():
    from app.core.config import RULE_VERSION
    assert RULE_VERSION == "tev-1.2.0"


def test_production_coverage_ratios_are_pinned():
    from app.verify.coverage import audit

    for name, answer, claims, expected in PRODUCTION_FIXTURES:
        report = audit(answer, claims)
        actual = (
            report.total_assertions,
            report.checked,
            report.candidates,
            report.claims_without_candidate,
            report.unscanned,
            round(report.coverage, 4),
            round(report.material_coverage, 4),
            dict(report.dispositions),
        )
        assert actual == expected, name


def test_verdict_decisions_are_unchanged(tmp_path):
    from eval.run_eval import run_hermetic_case

    path = ROOT / "eval" / "gold.jsonl"
    cases = {
        case["id"]: case
        for case in (json.loads(line) for line in
                     path.read_text(encoding="utf-8").splitlines() if line.strip())
    }
    for case_id in ("stable-fact-supported", "price-changed-after-generation",
                    "contradiction-only", "no-source"):
        case = cases[case_id]
        result = run_hermetic_case(case, tmp_path / case_id)
        assert result.run_status == "completed", case_id
        assert result.structural == [], case_id
        assert result.failures == [], case_id
        assert result.rows, case_id
        assert all(row["ok_status"] for row in result.rows), case_id
        assert result.quote_valid == result.quote_total, case_id


def test_existing_gold_files_and_gate_source_are_unchanged():
    for relative, expected in PINNED_FILES.items():
        assert _digest(ROOT / relative) == expected, relative
