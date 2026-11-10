"""Publication time and event time are different facts about a page."""
from __future__ import annotations

from datetime import date

import pytest

from app.verify.temporal import event_date_from_quote, find_date_expression

CONTENT = ("Acme pricing history. The Pro plan changed to $129 per month, "
           "effective November 7, 2026. The change was announced on 2026-11-05.")


def test_an_explicit_calendar_date_inside_the_quote_is_the_event_date():
    out = event_date_from_quote(
        "The Pro plan changed to $129 per month, effective November 7, 2026.",
        CONTENT)
    assert out.status == "explicit"
    assert out.value == date(2026, 11, 7)
    assert out.quote == "November 7, 2026"


def test_iso_dates_are_recognised():
    content = "Effective 2026-11-07, the Pro plan costs $129."
    out = event_date_from_quote("Effective 2026-11-07, the Pro plan costs $129.", content)
    assert out.value == date(2026, 11, 7)
    assert out.status == "explicit"


def test_day_month_year_is_recognised():
    content = "From 7 November 2026 the Pro plan costs $129."
    out = event_date_from_quote(content, content)
    assert out.value == date(2026, 11, 7)


def test_a_publication_date_is_never_used_as_the_event_date():
    """The single most expensive confusion in temporal verification."""
    quote = "The Pro plan now costs $129 per month."
    out = event_date_from_quote(quote, quote, published_date=date(2026, 11, 9))
    assert out.status == "missing"
    assert out.value is None


def test_a_relative_date_is_anchored_and_labelled_relative():
    content = "Posted today. The Pro plan changed yesterday to $129."
    out = event_date_from_quote(
        "The Pro plan changed yesterday to $129.", content,
        published_date=date(2026, 11, 9))
    assert out.status == "relative"
    assert out.value == date(2026, 11, 8)


def test_a_relative_date_without_an_anchor_is_missing():
    quote = "The Pro plan changed yesterday to $129."
    out = event_date_from_quote(quote, quote)
    assert out.status == "missing"
    assert out.value is None


@pytest.mark.parametrize("quote", [
    "The price changed recently.",
    "The price changed last month.",
    "The price is now $129 per month.",
])
def test_vague_phrases_do_not_produce_a_date(quote):
    assert event_date_from_quote(quote, quote).value is None


def test_a_quote_that_is_not_in_the_source_has_no_event_date():
    out = event_date_from_quote("changed on November 7, 2026", "unrelated text")
    assert out.status == "missing"
    assert out.value is None


def test_month_only_dates_are_not_promoted_to_a_day():
    assert find_date_expression("effective November 2026") is None
