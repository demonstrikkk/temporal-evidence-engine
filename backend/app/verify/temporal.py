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

#: Quarter patterns: "Q1 2026", "first quarter 2026", "Q4'25"
_QUARTER = re.compile(
    r"\bq([1-4])[\s'\-]*(20\d{2})\b", re.I)
_QUARTER_WORD = re.compile(
    r"\b(first|second|third|fourth)\s+quarter\s+(20\d{2})\b", re.I)

#: Fiscal year: "FY2026", "fiscal year 2026", "FY'26"
_FISCAL_YEAR = re.compile(
    r"\b(?:fy|fiscal\s+year)[\s'\-]*(20\d{2})\b", re.I)

#: Effective date phrases: "effective November 7, 2026", "from 2026-11-07",
#: "starting January 15, 2026", "as of November 7, 2026"
_EFFECTIVE = re.compile(
    r"\b(?:effective|from|starting|as of|commencing|in effect)\s+" +
    r"(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) +
    r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b", re.I)

_EFFECTIVE_ISO = re.compile(
    r"\b(?:effective|from|starting|as of|commencing|in effect)\s+" +
    r"(20\d{2})-(\d{2})-(\d{2})\b", re.I)

#: "in 2026", "during 2026", "by 2026" - year only, lower precision
_YEAR_ONLY = re.compile(r"\b(?:in|during|by)\s+(20\d{2})\b", re.I)

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
    status: Literal["explicit", "relative", "quarter", "fiscal_year", "year", "missing"]
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


def _quarter_to_month(q: str) -> int:
    return {"1": 1, "2": 4, "3": 7, "4": 10}[q]


def _quarter_word_to_month(word: str) -> int:
    return {"first": 1, "second": 4, "third": 7, "fourth": 10}[word.lower()]


def find_date_expression(text: str) -> tuple[date, str, str] | None:
    """First calendar date in `text` -> (date, matched_text, status).

    Status is one of: "explicit" (exact calendar date), "quarter" (Q1/Q2/Q3/Q4
    or first/second/third/fourth quarter), "fiscal_year" (FY2026), "year"
    (year-only, lower precision), "relative" (yesterday/today).
    """
    if not text:
        return None

    # 1. Exact ISO date: 2026-11-07
    m = _ISO.search(text)
    if m:
        found = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if found:
            return found, m.group(0), "explicit"

    # 2. Month Day, Year: November 7, 2026
    m = _MONTH_DAY_YEAR.search(text)
    if m:
        found = _valid(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
        if found:
            return found, m.group(0), "explicit"

    # 3. Day Month Year: 7 November 2026
    m = _DAY_MONTH_YEAR.search(text)
    if m:
        found = _valid(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
        if found:
            return found, m.group(0), "explicit"

    # 4. Quarter: Q1 2026, Q4'25, first quarter 2026
    m = _QUARTER.search(text)
    if m:
        q = m.group(1)
        year = int(m.group(2))
        month = _quarter_to_month(q)
        found = _valid(year, month, 1)
        if found:
            return found, m.group(0), "quarter"

    m = _QUARTER_WORD.search(text)
    if m:
        word = m.group(1)
        year = int(m.group(2))
        month = _quarter_word_to_month(word)
        found = _valid(year, month, 1)
        if found:
            return found, m.group(0), "quarter"

    # 5. Fiscal year: FY2026, fiscal year 2026 (treated as year-start)
    m = _FISCAL_YEAR.search(text)
    if m:
        year = int(m.group(1))
        found = _valid(year, 1, 1)
        if found:
            return found, m.group(0), "fiscal_year"

    # 6. Effective date phrases: effective November 7, 2026
    m = _EFFECTIVE.search(text)
    if m:
        found = _valid(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
        if found:
            return found, m.group(0), "explicit"

    m = _EFFECTIVE_ISO.search(text)
    if m:
        found = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if found:
            return found, m.group(0), "explicit"

    # 7. Year only: in 2026, during 2026 (lowest precision)
    m = _YEAR_ONLY.search(text)
    if m:
        year = int(m.group(1))
        found = _valid(year, 1, 1)
        if found:
            return found, m.group(0), "year"

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
        detail = {
            "explicit": "explicit calendar date in the quote",
            "quarter": "calendar quarter in the quote (treated as quarter start)",
            "fiscal_year": "fiscal year in the quote (treated as year start)",
            "year": "calendar year only in the quote (lower precision)",
        }.get(status, "date expression in the quote")
        return EventDate(value, content[abs_start:abs_end], status,
                         detail)

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
