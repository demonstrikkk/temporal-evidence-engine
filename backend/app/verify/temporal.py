"""Event dates, extracted from the source text that is actually stored.

A publication date is when a page was published or last updated. An event date is
when the fact changed. An article published on 9 November can describe a price
change that took effect on 7 November; using the publication date as the change
date makes an answer generated on 8 November look stale when it was already
stale on 7 November, and (worse) makes an answer generated on 8 November look
stale when the change actually happened on 10 November.

So: no event date is inferred from a publication date, ever. If the text does
not state one, `status="missing"` and the decision engine refuses `changed`.

Only two kinds of statement produce a date:

* an exact calendar date in the quote (`2026-11-07`, `November 7, 2026`);
* a deictic anchored to a *known* publication date (`yesterday`), which is
  recorded as `relative` so the UI can show how it was derived.

Anything else — "recently", "this week", "last month" — is missing. Manufacturing
a date from a vague phrase is how a verifier starts inventing changes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from app.schemas.evidence import locate_quote

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_MONTH_DAY_YEAR = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) +
    r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b", re.I)
_DAY_MONTH_YEAR = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" +
    "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?,?\s+(20\d{2})\b",
    re.I)
_RELATIVE = re.compile(r"\b(yesterday|today)\b", re.I)


@dataclass(frozen=True)
class EventDate:
    value: date | None
    quote: str | None
    status: Literal["explicit", "relative", "missing"]
    detail: str = ""

    @property
    def known(self) -> bool:
        return self.value is not None


MISSING = EventDate(None, None, "missing", "the source does not state when this became true")


def _valid(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_date_expression(text: str) -> tuple[date, str, str] | None:
    """First calendar date in `text` -> (date, matched_text, "explicit")."""
    if not text:
        return None
    m = _ISO.search(text)
    if m:
        found = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if found:
            return found, m.group(0), "explicit"

    m = _MONTH_DAY_YEAR.search(text)
    if m:
        found = _valid(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
        if found:
            return found, m.group(0), "explicit"

    m = _DAY_MONTH_YEAR.search(text)
    if m:
        found = _valid(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
        if found:
            return found, m.group(0), "explicit"
    return None


def event_date_from_quote(quote: str, content: str,
                          published_date: date | None = None) -> EventDate:
    """Resolve the event date stated *inside* a located evidence quote.

    The quote must already resolve against `content`: a date that cannot be
    located in stored text is not evidence of anything.
    """
    if not quote or not content:
        return MISSING
    start, end = locate_quote(quote, content)
    if start is None or end is None:
        return EventDate(None, None, "missing", "the quote does not resolve against stored text")

    found = find_date_expression(quote)
    if found:
        value, matched, status = found
        # Locate the date itself inside the quote, then map it back to the
        # absolute position in the stored text, so the date has its own proof.
        offset = quote.find(matched)
        abs_start = start + offset if offset >= 0 else start
        abs_end = abs_start + len(matched)
        return EventDate(value, content[abs_start:abs_end], status,
                         "explicit calendar date in the quote")

    rel = _RELATIVE.search(quote)
    if rel and published_date is not None:
        delta = -1 if rel.group(1).lower() == "yesterday" else 0
        value = published_date + timedelta(days=delta)
        return EventDate(value, rel.group(0), "relative",
                         f"anchored to the source publication date {published_date.isoformat()}")

    if rel and published_date is None:
        return EventDate(None, rel.group(0), "missing",
                         "a relative date with no publication date cannot be anchored")
    return MISSING


def effective_after(event: date | None, generated_at) -> bool:
    """Did the event happen strictly after the answer was generated?"""
    if event is None or generated_at is None:
        return False
    return event > generated_at.date()
