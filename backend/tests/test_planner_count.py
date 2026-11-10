"""A duration in the prompt is not a record count.

`_rule_plan` used to take the first 1-3 digit number anywhere in the prompt, so
"startups funded in the last 6 months" asked for six rows and carried no time
constraint at all. A four-digit year escaped only because `\\b` cannot sit
between the digits of `2024`.
"""
from __future__ import annotations

import pytest

from app.services.planner import _requested_count, _rule_plan


@pytest.mark.parametrize("prompt,expected", [
    ("startups funded in the last 6 months", 15),
    ("startups funded in the past 3 years", 15),
    ("analyze the last 12 months of data", 15),
    ("find 8 startups", 8),
    ("top 10 companies in the last 6 months", 10),
    ("collect 25 law firms founded since 2019", 25),
    ("companies from 2024", 15),
    ("5 companies that raised money 2 years ago", 5),
])
def test_requested_count_ignores_durations(prompt, expected):
    assert _requested_count(prompt) == expected
    assert _rule_plan(prompt)["requested_count"] == expected


def test_a_duration_still_produces_a_usable_plan():
    plan = _rule_plan("startups funded in the last 6 months")
    assert plan["requested_count"] == 15
    assert plan["max_results"] == 15
    assert plan["fields"][0]["required"] is True
