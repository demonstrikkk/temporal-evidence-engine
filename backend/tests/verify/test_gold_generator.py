"""The generated gold half must be exactly what the generator produces.

If this fails, someone edited `eval/gold_generated.jsonl` by hand - usually to
make a failing case pass. The expectations belong to the rules, so the file has to
be a function of the generator, not of the morning's results.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
GENERATOR = REPO / "eval" / "generate_cases.py"
GOLD = REPO / "eval" / "gold_generated.jsonl"

pytestmark = pytest.mark.skipif(
    not (GENERATOR.is_file() and GOLD.is_file()),
    reason="gold generator not present")


def _load_generator():
    spec = importlib.util.spec_from_file_location("tev_generate_cases", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_gold_file_is_what_the_generator_produces():
    module = _load_generator()
    built = module.build()
    committed = [json.loads(line) for line in
                 GOLD.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(committed) == len(built), (
        f"committed {len(committed)} cases, generator builds {len(built)}")
    for generated, stored in zip(built, committed):
        assert generated == stored, (
            f"case {stored.get('id')!r} in the committed file is not what the "
            "generator produces; regenerate instead of editing it")


def test_the_holdout_split_is_every_fourth_case():
    module = _load_generator()
    cases = module.build()
    for index, case in enumerate(cases):
        expected = "heldout" if index % 4 == 3 else "dev"
        assert case["split"] == expected, case["id"]


def test_no_generated_expectation_is_silently_missing():
    module = _load_generator()
    for case in module.build():
        assert case["claims"], case["id"]
        for claim in case["claims"]:
            expect = claim.get("expect") or {}
            assert expect.get("status") in {"supported", "changed",
                                            "contradicted", "unverifiable"}, \
                f"{case['id']} has no expected status"
            assert expect.get("reason_code"), f"{case['id']} has no reason code"


def test_every_proposal_quote_is_in_the_text_it_cites():
    """A gold case whose quote is not in its own source teaches the wrong thing."""
    module = _load_generator()
    for case in module.build():
        pages = {source["url"]: source["content"] for source in case["sources"]}
        for proposal in case.get("proposals", []):
            content = pages.get(proposal["url"])
            assert content is not None, f"{case['id']} cites an unknown url"
            if case["id"].startswith("invented-quote"):
                # This family exists because the quote is *not* in the page.
                assert proposal["quote"] not in content
                continue
            assert proposal["quote"] in content, (
                f"{case['id']}: quote is not a substring of the cited page")
