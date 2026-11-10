"""The citation invariant, measured over the legal corpus.

These are not gold labels and there is no model in the path. `validator.settle`
stores the located slice as the cell's quote, so `quote in text` is an exact,
zero-tolerance property of every cell it writes. A regression here is a real
citation defect, not a judgement call.

The negative-control test matters as much as the corpus test: a checker that
cannot fail is the "check that could not fail" pattern the repo already shipped
once.
"""
from __future__ import annotations

import json
import pathlib

from app.services import quality

CORPUS = (pathlib.Path(__file__).resolve().parents[2]
          / "editions" / "datagoblin_legal" / "corpus" / "documents.json")


def _documents() -> list[dict]:
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    return data.get("documents") or []


def _pairs():
    for doc in _documents():
        text = doc.get("text") or ""
        for cell in (doc.get("fields") or {}).values():
            yield cell, text


def test_corpus_is_present_and_not_empty():
    docs = _documents()
    assert docs, f"corpus missing or empty at {CORPUS}"
    assert any(doc.get("fields") for doc in docs)


def test_every_legal_corpus_cell_resolves_exactly():
    report = quality.score(_pairs())
    assert report["total"] > 0
    assert report["no_quote"] == 0
    assert report["unlocatable"] == 0
    assert report["offset_mismatch"] == 0
    assert report["resolved"] == report["valued"]
    # The corpus was built from stored text, so resolution is byte-identical
    # rather than merely normalised. A non-zero LOCATED count means a builder
    # started quoting text it had not stored.
    assert report["exact"] == report["valued"]


def test_the_checker_can_fail():
    """Negative control: a deliberately broken quote must be reported."""
    cell, text = next(_pairs())
    broken = {**cell, "quote": "this sentence is not in the stored document"}
    assert quality.check_cell(broken, text)["status"] == quality.UNLOCATABLE


def test_offset_mismatch_is_not_excused_by_a_locatable_quote():
    text = "alpha beta gamma"
    cell = {"value": "beta", "source": {"quote": "beta", "start": 0, "end": 4}}
    assert quality.check_cell(cell, text)["status"] == quality.OFFSET_MISMATCH


def test_exact_offsets_are_accepted_without_relocating():
    text = "alpha beta gamma"
    cell = {"value": "beta", "source": {"quote": "beta", "start": 6, "end": 10}}
    assert quality.check_cell(cell, text)["status"] == quality.EXACT


def test_absent_and_uncited_values_are_distinguished():
    assert quality.check_cell({"value": None}, "text")["status"] == quality.ABSENT
    assert quality.check_cell({"value": "x"}, "text")["status"] == quality.NO_QUOTE


def test_absent_cells_are_not_in_the_denominator():
    report = quality.score([({"value": None}, "text"),
                            ({"value": "x", "quote": "x"}, "text")])
    assert report["total"] == 2
    assert report["valued"] == 1
    assert report["resolution_rate"] == 1.0


def test_reliability_of_nothing_is_zero_not_one():
    assert quality.reliability([]) == 0.0


def test_bootstrap_interval_is_seeded_and_contains_the_mean():
    flags = [True] * 90 + [False] * 10
    first = quality.bootstrap_ci(flags, iters=500, seed=7)
    second = quality.bootstrap_ci(flags, iters=500, seed=7)
    assert first == second
    lo, hi = first
    assert lo <= 0.9 <= hi
    assert 0.0 <= lo <= hi <= 1.0
