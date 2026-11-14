"""The claim list carries a freshness score, and that score decays.

Freshness is the age of the last successful verification, not a probability that
the fact is true. Two properties matter enough to pin:

* a never-verified claim reports `None`, not `0` — "never checked" and "checked
  until worthless" are different states and the UI has to say which;
* the score is computed when it is read. A value stored at settlement would
  still read 100 a week later, which is exactly the staleness this product
  exists to detect.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.api.verify import _claim_view
from app.verify.freshness import half_life

NOW = datetime.now(timezone.utc)


def _claim(**overrides):
    base = {
        "id": "clm_1",
        "run_id": "run_1",
        "ordinal": 0,
        "claim_text": "ChatGPT Plus costs $20 per month.",
        "status": "supported",
        "reason_code": "supported_by_source",
        "volatility_class": "moderate",
        "last_verified_at": NOW,
    }
    base.update(overrides)
    return base


def test_a_claim_verified_moments_ago_is_fresh():
    view = _claim_view(_claim(last_verified_at=datetime.now(timezone.utc)))
    assert view["freshness_index"] >= 99


def test_freshness_halves_over_one_half_life():
    view = _claim_view(_claim(last_verified_at=NOW - half_life("moderate")))
    assert 45 <= view["freshness_index"] <= 55


def test_never_verified_is_none_rather_than_zero():
    view = _claim_view(_claim(last_verified_at=None))
    assert view["freshness_index"] is None


def test_an_iso_string_timestamp_scores_the_same_as_a_datetime():
    # The memory store and the SQL store disagree about the type; the score
    # must not depend on which one answered.
    moment = NOW - half_life("moderate")
    iso = moment.isoformat().replace("+00:00", "Z")
    from_datetime = _claim_view(_claim(last_verified_at=moment))["freshness_index"]
    from_string = _claim_view(_claim(last_verified_at=iso))["freshness_index"]
    assert abs(from_datetime - from_string) <= 1


def test_the_half_life_travels_with_the_claim():
    # The UI must not keep a second copy of the policy table and drift.
    assert _claim_view(_claim(volatility_class="stable"))["half_life_hours"] == 720.0
    assert _claim_view(_claim(volatility_class="ephemeral"))["half_life_hours"] == 0.25


def test_an_unparseable_timestamp_does_not_invent_freshness():
    view = _claim_view(_claim(last_verified_at="not a date"))
    assert view["freshness_index"] is None
    assert view["last_verified_at"] == "not a date"
