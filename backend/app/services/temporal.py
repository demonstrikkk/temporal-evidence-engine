"""Prompt time -> `TemporalSpec`, and the date check every cell can carry.

Three rules keep this deterministic and safe.

**Only unambiguous phrasing becomes a constraint.** "as of 2024", "last six
months", "between 2020 and 2023" describe the state of the world the request
wants. "companies founded in 2020" describes a *field*, and reading that year as
an as-of date would silently drop every source outside 2020. Bare years are
therefore not parsed; neither is a bare "in 2024".

**A missing date is not a failure.** A cell whose page published no date gets
`status: "unknown"`, not `"outside"`. The search engine does not always expose a
date, and pretending it does turns an absence into a wrong claim.

**A temporal mismatch never changes `verification_status`.** The quote either
locates on the stored page or it does not; that is what verified means. A page
from the wrong year is a relevance finding, and `validator.settle` remains the
only place a cell can be promoted. `check` returns a separate record.

`valid_time` is stored beside `source`, not inside it: retrieval time says when
we looked, validity says when the fact was true.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
from email.utils import parsedate_to_datetime
from typing import Any

from app.schemas.plan import TemporalSpec

__all__ = ["parse", "parse_date", "normalize_date", "valid_interval", "check",
           "search_window", "summary"]

_MONTH_NAMES = {}
for _i in range(1, 13):
    _MONTH_NAMES[calendar.month_name[_i].lower()] = _i
    _MONTH_NAMES[calendar.month_abbr[_i].lower()] = _i
_MONTH_NAMES["sept"] = 9
_MONTH_ALT = "|".join(sorted((re.escape(m) for m in _MONTH_NAMES), key=len,
                             reverse=True))

#: `(?!\d)` rather than `\b` at the end: an ISO date is routinely followed by
#: `T10:00:00Z`, and a word boundary does not exist between `3` and `T`.
#: `[./-]` rather than `-`: `2025/09/26` is written by CMSes and models, and
#: reading only the year out of it produced January 1.
_ISO = re.compile(r"\b((?:19|20)\d{2})[./-](\d{1,2})[./-](\d{1,2})(?!\d)")
#: `2024-06`: a month, no day. Without this the bare-year fallback answered
#: January 1, which moved a requested June window to the start of the year.
_ISO_MONTH = re.compile(r"\b((?:19|20)\d{2})[./-](\d{1,2})(?![\d./-])")
_DMY = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-]((?:19|20)\d{2})(?!\d)")
#: A written date with the month spelled out: `September 26, 2025` and
#: `26 September 2025`. A page's display string and the model's echo of it both
#: use these forms, so they arrive alongside ISO dates for the very same day.
#: Without them `_MONTH_YEAR` did not match (no year directly after the month)
#: and the bare-year fallback turned the whole thing into January 1 — a silently
#: wrong date, which is worse than no date at all.
_MONTH_DAY = re.compile(
    rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?!\d)(?:\s*,\s*|\s+)"
    rf"((?:19|20)\d{{2}})\b", re.I)
_DAY_MONTH = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_ALT})\.?,?\s+"
    rf"((?:19|20)\d{{2}})\b", re.I)
_MONTH_YEAR = re.compile(rf"\b({_MONTH_ALT})\.?\s+((?:19|20)\d{{2}})\b", re.I)
_YEAR_MONTH = re.compile(rf"\b((?:19|20)\d{{2}})\s+({_MONTH_ALT})\b", re.I)
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")

_AS_OF = re.compile(
    r"\bas\s+(?:of|at|on)\s+"
    r"(?P<when>today|now|the\s+present|"
    r"(?:19|20)\d{2}-\d{1,2}-\d{1,2}|"
    r"\d{1,2}[./-]\d{1,2}[./-](?:19|20)\d{2}|"
    rf"(?:{_MONTH_ALT})\.?\s+(?:19|20)\d{{2}}|"
    rf"(?:19|20)\d{{2}}\s+(?:{_MONTH_ALT})|"
    r"(?:19|20)\d{2})", re.I)

# `,` is not a terminator: a written date contains one (`May 1, 2024`), and
# treating it as the end of the bound truncated the year off `June 1, 2024`,
# leaving an unreadable bound and a window that silently became "no time".
_BETWEEN = re.compile(
    r"\bbetween\s+(?P<a>.+?)\s+and\s+(?P<b>.+?)(?=[.;]|$)", re.I)
_FROM_TO = re.compile(
    r"\bfrom\s+(?P<a>.+?)\s+(?:to|through|until|till)\s+(?P<b>.+?)(?=[.;]|$)",
    re.I)
_SINCE = re.compile(r"\bsince\s+(?P<a>.+?)(?=[.;]|$)", re.I)

_DURATION = re.compile(
    r"\b(?P<rel>last|past|previous|within|over|next)\s+"
    r"(?P<n>\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|twelve)\s+"
    r"(?P<unit>day|week|month|quarter|year)s?\b", re.I)

_THIS = re.compile(r"\bthis\s+(?P<unit>week|month|quarter|year)\b", re.I)
_QUARTER = re.compile(r"\bQ(?P<q>[1-4])\s*(?P<y>(?:19|20)\d{2})\b", re.I)
_HALF = re.compile(
    r"\b(?P<h>H[12])\s*(?P<y>(?:19|20)\d{2})\b"
    r"|\b(?P<ord>first|second)\s+half\s+of\s+(?P<y2>(?:19|20)\d{2})\b", re.I)
_CURRENT = re.compile(
    r"\b(?:current(?:ly)?|today|right\s+now|latest|most\s+recent)\b", re.I)

_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                 "twelve": 12}


def _today(today: dt.date | None = None) -> dt.date:
    return today or dt.date.today()


def _safe_date(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(int(year), int(month), min(int(day),
                                                 calendar.monthrange(int(year), int(month))[1]))
    except (ValueError, TypeError):
        return None


def _shift_months(day: dt.date, months: int) -> dt.date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _month_end(year: int, month: int) -> dt.date:
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def _exact_date(year: int, month: int, day: int) -> dt.date | None:
    """The date only if it exists. Unlike `_safe_date` it never clamps a day."""
    try:
        return dt.date(int(year), int(month), int(day))
    except (ValueError, TypeError):
        return None


def _read_date(text: str) -> tuple[dt.date | None, str]:
    """First date in `text`, with its precision ("day", "month", "year")."""
    s = str(text or "")
    m = _ISO.search(s)
    if m:
        d = _safe_date(*m.groups())
        return d, "day" if d else ""
    m = _DMY.search(s)
    if m:
        a, b, year = (int(x) for x in m.groups())
        # Day-first, then month-first if that is not a real date. A bare
        # "03/04/2024" is genuinely ambiguous; the international reading is the
        # one that does not silently swap April for March. "29-09-2026" cannot
        # be month-first at all (month 29), which is exactly how the fallback
        # tells the two apart without guessing.
        d = _safe_date(year, b, a) or _safe_date(year, a, b)
        return d, "day" if d else ""
    # Month spelled out, either order. Checked before the month-only patterns,
    # which would otherwise answer "September 26, 2025" with September's first
    # day and JSON with the bare-year fallback (January 1).
    m = _MONTH_DAY.search(s) or _DAY_MONTH.search(s)
    if m:
        a, b, year = m.groups()
        if a.isdigit():  # day first: `26 September 2025`
            day, name = int(a), b.lower()
        else:            # month first: `September 26, 2025`
            day, name = int(b), a.lower()
        month = _MONTH_NAMES.get(name)
        # A day the month does not have is not a date. Clamping it (Feb 31 ->
        # Feb 28) invents a day the text never claimed; the honest answer is
        # "unreadable", which the caller records as `unknown`.
        d = _exact_date(year, month, day) if month else None
        return d, "day" if d else ""
    m = _ISO_MONTH.search(s)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return dt.date(year, month, 1), "month"
    m = _MONTH_YEAR.search(s)
    if m:
        name, year = m.group(1).lower(), int(m.group(2))
        month = _MONTH_NAMES.get(name)
        return (dt.date(year, month, 1), "month") if month else (None, "")
    m = _YEAR_MONTH.search(s)
    if m:
        year, name = int(m.group(1)), m.group(2).lower()
        month = _MONTH_NAMES.get(name)
        return (dt.date(year, month, 1), "month") if month else (None, "")
    m = _YEAR.search(s)
    if m:
        return dt.date(int(m.group(1)), 1, 1), "year"
    return None, ""


def parse_date(value: Any, today: dt.date | None = None) -> dt.date | None:
    """A date from the shapes providers actually send, or None.

    Tavily's `published_date` is usually ISO but occasionally an RFC-2822 string.
    A bare year is accepted as January 1 for comparison; `valid_interval` keeps
    the coarser precision so a year is never treated as a single day.
    """
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    s = str(value or "").strip()
    if not s:
        return None
    m = _ISO.match(s)
    if m:
        return _safe_date(*m.groups())
    try:
        return parsedate_to_datetime(s).date()
    except (TypeError, ValueError, OverflowError):
        pass
    d, _precision = _read_date(s)
    return d


def valid_interval(value: Any) -> tuple[dt.date, dt.date, str] | None:
    """The date range a stored `valid_time` stands for, plus its precision.

    A full date is one day. `2024-03` is that month. `2024` is the whole year,
    which is why a year-precision value compared to a mid-year as-of date is
    "uncertain" rather than a confident "within".
    """
    s = str(value or "").strip()
    if not s:
        return None
    m = re.match(r"^((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        d = _safe_date(*m.groups())
        return (d, d, "day") if d else None
    m = re.match(r"^((?:19|20)\d{2})-(\d{1,2})$", s)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        return dt.date(year, month, 1), _month_end(year, month), "month"
    m = re.match(r"^((?:19|20)\d{2})$", s)
    if m:
        year = int(m.group(1))
        return dt.date(year, 1, 1), dt.date(year, 12, 31), "year"
    d = parse_date(s)
    return (d, d, "day") if d else None


def _as_spec(spec: Any) -> TemporalSpec:
    if isinstance(spec, TemporalSpec):
        return spec
    if isinstance(spec, dict):
        try:
            return TemporalSpec(**spec)
        except Exception:  # noqa: BLE001 (a malformed plan is "no constraint")
            return TemporalSpec()
    return TemporalSpec()


def parse(text: str, today: dt.date | None = None) -> TemporalSpec:
    """Read the requested time out of a prompt. Never raises."""
    prompt = str(text or "")
    now = _today(today)
    try:
        return _parse(prompt, now)
    except Exception:  # noqa: BLE001 (a parser failure is not a run failure)
        return TemporalSpec()


def _parse(prompt: str, today: dt.date) -> TemporalSpec:
    m = _AS_OF.search(prompt)
    if m:
        when = m.group("when").lower()
        if when in ("today", "now", "the present"):
            return TemporalSpec(kind="current", as_of=today.isoformat(),
                                phrase=m.group(0), source="prompt")
        d, precision = _read_date(when)
        if d:
            if precision == "year":
                d = dt.date(d.year, 12, 31)
            return TemporalSpec(kind="point", as_of=d.isoformat(),
                                phrase=m.group(0), source="prompt")

    m = _BETWEEN.search(prompt)
    if m:
        start, end = _window_bounds(m.group("a"), m.group("b"))
        if start and end:
            return TemporalSpec(kind="window", start=start, end=end,
                                phrase=m.group(0), source="prompt")

    m = _FROM_TO.search(prompt)
    if m:
        start, end = _window_bounds(m.group("a"), m.group("b"))
        if start and end:
            return TemporalSpec(kind="window", start=start, end=end,
                                phrase=m.group(0), source="prompt")

    m = _SINCE.search(prompt)
    if m:
        d, _precision = _read_date(m.group("a"))
        if d:
            return TemporalSpec(kind="window", start=d.isoformat(),
                                end=today.isoformat(), phrase=m.group(0),
                                source="prompt")

    m = _DURATION.search(prompt)
    if m:
        n = _WORD_NUMBERS.get(m.group("n").lower())
        n = int(m.group("n")) if n is None else n
        unit = m.group("unit").lower()
        rel = m.group("rel").lower()
        if rel == "next":
            end = _add_units(today, unit, n)
            return TemporalSpec(kind="window", start=today.isoformat(),
                                end=end.isoformat(), phrase=m.group(0),
                                source="prompt")
        start = _add_units(today, unit, -abs(n))
        return TemporalSpec(kind="window", start=start.isoformat(),
                            end=today.isoformat(), phrase=m.group(0),
                            source="prompt")

    m = _THIS.search(prompt)
    if m:
        unit = m.group("unit").lower()
        start, end = _this_period(today, unit)
        return TemporalSpec(kind="window", start=start.isoformat(),
                            end=end.isoformat(), phrase=m.group(0),
                            source="prompt")

    m = _QUARTER.search(prompt)
    if m:
        q, year = int(m.group("q")), int(m.group("y"))
        start = dt.date(year, 3 * (q - 1) + 1, 1)
        end = _month_end(year, 3 * q)
        return TemporalSpec(kind="window", start=start.isoformat(),
                            end=end.isoformat(), phrase=m.group(0),
                            source="prompt")

    m = _HALF.search(prompt)
    if m:
        year = int(m.group("y") or m.group("y2"))
        half = 1 if (m.group("h") or m.group("ord") or "").lower() in ("h1", "first") else 2
        start = dt.date(year, 1 if half == 1 else 7, 1)
        end = _month_end(year, 6 if half == 1 else 12)
        return TemporalSpec(kind="window", start=start.isoformat(),
                            end=end.isoformat(), phrase=m.group(0),
                            source="prompt")

    m = _CURRENT.search(prompt)
    if m:
        return TemporalSpec(kind="current", as_of=today.isoformat(),
                            phrase=m.group(0), source="prompt")

    return TemporalSpec()


def _add_units(day: dt.date, unit: str, amount: int) -> dt.date:
    if unit == "day":
        return day + dt.timedelta(days=amount)
    if unit == "week":
        return day + dt.timedelta(weeks=amount)
    if unit == "month":
        return _shift_months(day, amount)
    if unit == "quarter":
        return _shift_months(day, amount * 3)
    return _shift_months(day, amount * 12)


def _this_period(today: dt.date, unit: str) -> tuple[dt.date, dt.date]:
    if unit == "week":
        start = today - dt.timedelta(days=today.weekday())
        return start, start + dt.timedelta(days=6)
    if unit == "month":
        return today.replace(day=1), _month_end(today.year, today.month)
    if unit == "quarter":
        first = 3 * ((today.month - 1) // 3) + 1
        return dt.date(today.year, first, 1), _month_end(today.year, first + 2)
    return dt.date(today.year, 1, 1), dt.date(today.year, 12, 31)


def _window_bounds(a: str, b: str) -> tuple[str, str]:
    """Whole-period bounds for a window, so "2020 to 2023" includes all of 2023."""
    start_d, start_p = _read_date(a)
    end_d, end_p = _read_date(b)
    if not start_d or not end_d:
        return "", ""
    if start_p == "year":
        start_d = dt.date(start_d.year, 1, 1)
    elif start_p == "month":
        start_d = dt.date(start_d.year, start_d.month, 1)
    if end_p == "year":
        end_d = dt.date(end_d.year, 12, 31)
    elif end_p == "month":
        end_d = _month_end(end_d.year, end_d.month)
    if end_d < start_d:
        start_d, end_d = end_d, start_d
    return start_d.isoformat(), end_d.isoformat()


def search_window(spec: Any) -> tuple[str, str]:
    """The `start_date`/`end_date` to hand a search engine, or empty strings.

    `current` gets no window: recency is what the engine's own ranking is for,
    and inventing a one-year cut-off would silently exclude the authoritative
    page that happens to be older.
    """
    s = _as_spec(spec)
    if s.kind == "window" and s.start and s.end:
        return s.start, s.end
    if s.kind == "point" and s.as_of:
        return "", s.as_of
    return "", ""


def summary(spec: Any) -> str:
    s = _as_spec(spec)
    if s.kind == "current":
        return f"as of {s.as_of or 'today'}"
    if s.kind == "point":
        return f"as of {s.as_of}"
    if s.kind == "window":
        return f"{s.start} to {s.end}"
    return ""


def normalize_date(value: Any) -> str:
    """A stored `valid_time` in ISO form, or the original text when unreadable.

    Providers send dates in three shapes (`2024-09-03`, an ISO datetime, and
    RFC 2822 like `Tue, 11 Mar 2025 17:00:00 GMT`). Storing the raw string makes
    every reader re-parse it and makes the UI show `Tue, 11 Ma`. Normalising on
    write is the one place that can get it wrong once.
    """
    d = parse_date(value)
    return d.isoformat() if d else str(value or "").strip()


def check(valid_time: Any, spec: Any) -> dict:
    """Compare a cell's `valid_time` with the requested window.

    Returns `{}` when the request named no time. Otherwise a standalone record
    that a caller may store beside the cell; it never edits
    `verification_status`.
    """
    s = _as_spec(spec)
    if s.kind == "none":
        return {}
    base = {"kind": s.kind, "as_of": s.as_of, "start": s.start, "end": s.end,
            "valid_time": str(valid_time or "")}
    iv = valid_interval(valid_time)
    if iv is None:
        return {**base, "status": "unknown"}
    v_start, v_end, precision = iv
    if s.kind == "window" and s.start and s.end:
        w_start = parse_date(s.start)
        w_end = parse_date(s.end)
        if not w_start or not w_end:
            return {**base, "status": "unknown"}
        if v_end < w_start:
            status = "before"
        elif v_start > w_end:
            status = "after"
        elif v_start >= w_start and v_end <= w_end:
            status = "within"
        else:
            status = "uncertain"
        age = (w_end - v_end).days
    else:
        anchor = parse_date(s.as_of) or dt.date.today()
        if v_end <= anchor:
            status = "within"
        elif v_start > anchor:
            status = "after"
        else:
            status = "uncertain"
        age = (anchor - v_end).days
    return {**base, "status": status, "precision": precision, "age_days": age}
