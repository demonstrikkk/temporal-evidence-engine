"""Deterministic quality accounting over stored evidence.

The suite has always tested invariants — "a verified cell has a locatable quote"
— and never measured extraction *quality*, so a change that made every page
extract one fewer field passed. This is the missing reader.

Nothing here calls a model, a network, a database, or a clock. It answers one
question per cell: does the citation still resolve against the stored text? That
check is free and zero-tolerance because `validator.settle` establishes it at
write time — on the verified path it stores `source_text[start:end]` as the
quote (`validator.py:536`), so `text[start:end] == quote` is exact by
construction and a mismatch is a real regression, not a fuzzy threshold.

Counts are returned alongside every rate on purpose. A rate of 1.0 over four
cells says nothing, and a count of zero mismatches is the statement worth
ratcheting.
"""
from __future__ import annotations

import random
from typing import Any, Iterable

from app.schemas.evidence import locate_quote

#: Why a cell did or did not resolve. A closed set so a report can group without
#: parsing prose.
ABSENT = "absent"              # no value: nothing to cite, and that is honest
NO_QUOTE = "no_quote"          # a value with no citation at all
NO_TEXT = "no_text"            # the page text the citation addresses is missing
UNLOCATABLE = "unlocatable"    # the quote is not in the text, even normalised
OFFSET_MISMATCH = "offset_mismatch"  # offsets exist but slice to different text
LOCATED = "located"            # only resolves through the locator's normalisation
EXACT = "exact"                # byte-identical slice

STATUSES = (ABSENT, NO_QUOTE, NO_TEXT, UNLOCATABLE, OFFSET_MISMATCH,
            LOCATED, EXACT)
RESOLVED = frozenset({EXACT, LOCATED})


def _quote_of(cell: dict) -> str:
    src = cell.get("source") if isinstance(cell, dict) else None
    src = src if isinstance(src, dict) else {}
    return str(src.get("quote") or cell.get("quote") or "")


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return not value
    return False


def check_cell(cell: dict, text: str) -> dict:
    """One cell against the text it claims to cite.

    Returns `{"status": <one of STATUSES>, "detail": str}`. Never raises: a
    malformed cell is a finding, not a crash, because this runs over stored data
    written by earlier versions.
    """
    if not isinstance(cell, dict):
        return {"status": ABSENT, "detail": "not a cell object"}
    quote = _quote_of(cell)
    if _is_empty(cell.get("value")):
        if not quote:
            return {"status": ABSENT, "detail": "no value"}
        # A quote with no value is still a citation worth checking; fall through.
    if not quote:
        return {"status": NO_QUOTE, "detail": "value has no quote"}
    if not text:
        return {"status": NO_TEXT, "detail": "no stored text"}

    src = cell.get("source") if isinstance(cell.get("source"), dict) else {}
    start, end = src.get("start"), src.get("end")
    if isinstance(start, int) and isinstance(end, int):
        if 0 <= start <= end <= len(text):
            if text[start:end] == quote:
                return {"status": EXACT, "detail": ""}
            return {"status": OFFSET_MISMATCH,
                    "detail": "stored offsets slice to different text"}
        return {"status": OFFSET_MISMATCH,
                "detail": "stored offsets fall outside the text"}

    found_start, found_end = locate_quote(quote, text)
    if found_start is None or found_end is None:
        return {"status": UNLOCATABLE, "detail": "quote not found in the text"}
    if text[found_start:found_end] == quote:
        return {"status": EXACT, "detail": ""}
    return {"status": LOCATED, "detail": "resolved after normalisation"}


def exact_locator(cell: dict, text: str) -> bool:
    """True only when the stored slice is byte-identical to the quote."""
    return check_cell(cell, text)["status"] == EXACT


def score(pairs: Iterable[tuple[dict, str]]) -> dict:
    """Counts + rates over `(cell, text)` pairs. The counts are the report."""
    counts = {status: 0 for status in STATUSES}
    for cell, text in pairs:
        counts[check_cell(cell, text)["status"]] += 1
    total = sum(counts.values())
    valued = total - counts[ABSENT]
    resolved = counts[EXACT] + counts[LOCATED]
    return {
        **counts,
        "total": total,
        "valued": valued,
        "resolved": resolved,
        "resolution_rate": (resolved / valued) if valued else 0.0,
        "exact_rate": (counts[EXACT] / valued) if valued else 0.0,
    }


def reliability(flags: Iterable[bool]) -> float:
    """Fraction of cells that resolved. Empty input is 0.0, not a vacuous 1.0."""
    data = [bool(f) for f in flags]
    return (sum(data) / len(data)) if data else 0.0


def bootstrap_ci(flags: Iterable[bool], *, iters: int = 2000, alpha: float = 0.05,
                 seed: int = 0) -> tuple[float, float]:
    """Percentile bootstrap interval for a proportion, deterministic per seed.

    Seeded on purpose: a quality gate that returns a different interval each run
    cannot be ratcheted, and the repo has no CI to re-run it on failure.
    """
    data = [1 if f else 0 for f in flags]
    n = len(data)
    if n == 0:
        return (0.0, 0.0)
    if n == 1:
        return (float(data[0]), float(data[0]))
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(max(1, int(iters))):
        means.append(sum(data[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo_idx = int((alpha / 2) * len(means))
    hi_idx = min(len(means) - 1, int((1 - alpha / 2) * len(means)))
    return (means[lo_idx], means[hi_idx])
