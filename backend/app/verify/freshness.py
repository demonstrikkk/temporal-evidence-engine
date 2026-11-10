"""Freshness is not confidence, and a score is not a probability.

Freshness answers "how old is the last successful verification of this claim?".
Evidence confidence answers "how much does the stored evidence support this
conclusion?". They move independently: a claim can be freshly verified against a
weak source, or verified long ago against a primary one.

The half-life form is used because it is explainable to a reader ("at one
half-life this is 50") and because a decay parameter per volatility class is a
product policy, not a fitted statistic. Nothing here is calibrated against
outcomes, so nothing here may be labelled a probability of truth.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.verify.schemas import HALF_LIFE_HOURS, Volatility

#: A claim is never due sooner than this, so a burst of `ephemeral` claims in one
#: answer cannot schedule a recheck loop every few minutes.
MIN_RECHECK = timedelta(hours=1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def half_life(volatility: Volatility | str) -> timedelta:
    cls = volatility if isinstance(volatility, Volatility) else Volatility.parse(volatility)
    return timedelta(hours=HALF_LIFE_HOURS[cls])


def freshness_index(volatility: Volatility | str,
                    last_verified_at: datetime | None,
                    *, now: datetime | None = None) -> int:
    """0–100, halving every half-life. `None` when never verified.

    Returning `None` rather than 0 matters: "never checked" and "checked so long
    ago it is worthless" are different states, and the UI must say which.
    """
    last = as_utc(last_verified_at)
    if last is None:
        return 0
    now = as_utc(now) or _now()
    age = max(0.0, (now - last).total_seconds())
    hl = half_life(volatility).total_seconds()
    if hl <= 0:
        return 0
    return int(round(100 * (0.5 ** (age / hl))))


def next_check_at(volatility: Volatility | str,
                  last_verified_at: datetime | None,
                  *, now: datetime | None = None) -> datetime | None:
    """When a recheck is *recommended*. Never a promise that one will happen.

    Half of the half-life is the policy: recheck before the evidence is worth
    half of what it was. Clamped to `MIN_RECHECK` so the schedule is sane.
    """
    last = as_utc(last_verified_at)
    if last is None:
        return None
    due = last + half_life(volatility) / 2
    due = max(due, last + MIN_RECHECK)
    now = as_utc(now) or _now()
    return due


def confidence_label(*, settled_primary: int, settled_secondary: int,
                     conflicting: bool) -> str:
    """`strong | moderate | weak` from evidence shape, not from a model's mood."""
    if conflicting:
        return "weak"
    if settled_primary >= 1 and settled_secondary >= 1:
        return "strong"
    if settled_primary >= 1 or settled_secondary >= 2:
        return "moderate"
    if settled_secondary >= 1:
        return "weak"
    return "weak"
