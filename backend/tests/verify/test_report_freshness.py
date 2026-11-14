"""The exported report states freshness per claim, and never invents a zero.

A claim that was never settled has no freshness age to score. The report prints
"never checked" for it; printing `0` would label it as checked-and-worthless,
which is a different and harsher statement.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.verify.freshness import half_life
from app.verify.report import _freshness_lines

NOW = datetime.now(timezone.utc)


def _claim(**overrides):
    base = {
        "id": "clm_abcdef1234",
        "volatility_class": "moderate",
        "last_verified_at": NOW,
        "next_check_at": NOW + timedelta(hours=12),
    }
    base.update(overrides)
    return base


def test_a_never_verified_claim_reads_as_never_checked():
    text = "\n".join(_freshness_lines([_claim(last_verified_at=None,
                                              next_check_at=None)], now=NOW))
    assert "never checked" in text
    assert "| 0 |" not in text


def test_a_fresh_claim_carries_its_score_and_due_flag():
    text = "\n".join(_freshness_lines([_claim()], now=NOW))
    assert "| 100 |" in text
    # Its recheck is recommended for later, so it is not due yet.
    assert "| no |" in text


def test_a_claim_past_one_half_life_has_decayed():
    stale = _claim(last_verified_at=NOW - half_life("moderate"))
    text = "\n".join(_freshness_lines([stale], now=NOW))
    assert "| 50 |" in text


def test_a_due_claim_is_flagged_and_explained():
    due = _claim(next_check_at=NOW - timedelta(hours=1))
    text = "\n".join(_freshness_lines([due], now=NOW))
    assert "| yes |" in text
    # The distinction the product exists to protect: an old check is due, not
    # false.
    assert "A stale claim is not wrong" in text
