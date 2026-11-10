"""Freshness decays; confidence does not. Neither is a probability of truth."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.verify.freshness import (confidence_label, freshness_index, half_life,
                                  next_check_at)
from app.verify.schemas import Volatility

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def test_freshness_halves_over_one_half_life():
    last = NOW - half_life(Volatility.HIGH)
    assert freshness_index(Volatility.HIGH, last, now=NOW) == 50


def test_freshness_is_full_immediately_after_a_check():
    assert freshness_index(Volatility.MODERATE, NOW, now=NOW) == 100


def test_never_verified_is_zero_and_has_no_due_date():
    assert freshness_index(Volatility.HIGH, None, now=NOW) == 0
    assert next_check_at(Volatility.HIGH, None, now=NOW) is None


def test_next_check_is_half_a_half_life_out():
    last = NOW - timedelta(hours=2)
    due = next_check_at(Volatility.HIGH, last, now=NOW)
    assert due == last + timedelta(hours=12)


def test_ephemeral_claims_are_not_scheduled_more_often_than_an_hour():
    last = NOW
    due = next_check_at(Volatility.EPHEMERAL, last, now=NOW)
    assert due == last + timedelta(hours=3)
    assert due >= last + timedelta(hours=1)


def test_naive_timestamps_are_treated_as_utc():
    naive = datetime(2026, 11, 9, 11, 0)
    assert freshness_index(Volatility.HIGH, naive, now=NOW) > 90


def test_confidence_is_a_label_not_a_percentage():
    assert confidence_label(settled_primary=1, settled_secondary=1,
                            conflicting=False) == "strong"
    assert confidence_label(settled_primary=0, settled_secondary=1,
                            conflicting=False) == "weak"
    assert confidence_label(settled_primary=1, settled_secondary=1,
                            conflicting=True) == "weak"
