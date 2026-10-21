from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

SOURCES = Path(__file__).resolve().parents[1] / "sources.json"


def _documents() -> list[dict]:
    return json.loads(SOURCES.read_text(encoding="utf-8"))["documents"]


def test_manifest_has_the_planned_mix() -> None:
    documents = _documents()

    assert len(documents) == 68
    assert len({document["doc_id"] for document in documents}) == 68
    assert Counter(document["doc_type"] for document in documents) == Counter(
        {"statute": 51, "judgment": 14, "constitution": 2, "fee_schedule": 1}
    )


def test_every_source_answered_without_issues() -> None:
    bad = [
        (document["doc_id"], document["probe"].get("status"), document["probe"]["issues"])
        for document in _documents()
        if document["probe"].get("status") != 200 or document["probe"]["issues"]
    ]

    assert bad == []


def test_no_source_is_explicitly_disallowed_by_robots() -> None:
    denied = [
        document["doc_id"]
        for document in _documents()
        if document["probe"].get("robots") == "deny"
    ]

    assert denied == []


def test_every_judgment_has_identity_and_extractable_text() -> None:
    judgments = [d for d in _documents() if d["doc_type"] == "judgment"]

    assert len(judgments) == 14
    for judgment in judgments:
        assert judgment["court"]
        assert judgment["citation"]
        assert judgment["probe"]["pages"] > 0
        assert judgment["probe"]["text_chars"] >= 40
