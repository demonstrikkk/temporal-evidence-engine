"""Typed value comparison — the difference between `changed` and a guess.

String equality produces two opposite, expensive errors:

* `$99/month` vs `99 USD per month` compared unequal -> a supported claim is
  reported as contradicted because the source formatted the same fact
  differently.
* `$99/month` vs `$99/month per seat` compared equal -> a real scope change is
  silently accepted.

So both sides are normalised to a typed shape first, and the comparison is
field-by-field. Anything that cannot be compared with confidence returns
`equivalent=False` plus a reason, and the caller abstains rather than guessing.

No currency conversion happens here. A rate needs a date and a source; without
one the honest answer is `scope_mismatch`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Currency glyphs, mapped only where the glyph is unambiguous within the
#: product's default locale. `$` is flagged ambiguous instead of silently
#: becoming USD: it is also CAD, AUD, NZD, SGD, HKD and MXN.
_GLYPHS = {
    "US$": ("USD", False), "USD": ("USD", False),
    "$": ("USD", True),
    "€": ("EUR", False), "EUR": ("EUR", False),
    "£": ("GBP", False), "GBP": ("GBP", False),
    "₹": ("INR", False), "INR": ("INR", False),
}

_PERIODS = {
    "month": "month", "mo": "month", "monthly": "month", "per month": "month",
    "year": "year", "yr": "year", "annual": "year", "yearly": "year",
    "annum": "year", "p.a": "year", "pa": "year",
    # A billing note is a period word too: "$10 per month (billed annually)"
    # states a monthly price. Without the adverb the whole value failed to
    # parse and fell back to string equality.
    "annually": "year", "quarterly": "quarter",
    "week": "week", "wk": "week", "weekly": "week",
    "day": "day", "daily": "day",
}

_UNITS = {
    "seat": "seat", "seats": "seat", "user": "user", "users": "user",
    "member": "member", "members": "member", "employee": "employee",
    "employees": "employee", "person": "person", "people": "person",
    "unit": "unit", "units": "unit",
}

_DATA = {"b": 1, "kb": 1000, "mb": 1000 ** 2, "gb": 1000 ** 3,
         "tb": 1000 ** 4, "kib": 1024, "mib": 1024 ** 2,
         "gib": 1024 ** 3, "tib": 1024 ** 4}
_TIME = {"s": 1, "sec": 1, "second": 1, "min": 60, "minute": 60,
         "h": 3600, "hr": 3600, "hour": 3600, "day": 86400}

_NUM = r"\d[\d,]*(?:\.\d+)?"


def _marks(table: dict[str, str]) -> dict[str, tuple[str, ...]]:
    """Invert a parser table into the surface forms a passage might contain.

    Parsing a value and *detecting* one in a quote need the same vocabulary.
    Keeping two hand-written copies is what let `member` be a valid per-unit for
    parsing while settlement still refused every `per member` quote: the second
    table had never heard of it. Deriving one from the other makes that class of
    drift impossible.
    """
    out: dict[str, list[str]] = {}
    for word, canonical in table.items():
        out.setdefault(canonical, []).append(word)
    return {key: tuple(dict.fromkeys(words)) for key, words in out.items()}


#: `USD` -> `("$", "us$", "usd")`, `month` -> `("month", "mo", "monthly", ...)`.
CURRENCY_MARKS: dict[str, tuple[str, ...]] = _marks(
    {glyph.lower(): code for glyph, (code, _amb) in _GLYPHS.items()})
PERIOD_MARKS: dict[str, tuple[str, ...]] = _marks(_PERIODS)
UNIT_MARKS: dict[str, tuple[str, ...]] = _marks(_UNITS)


def mentions(text: str, marks: tuple[str, ...] | list[str]) -> bool:
    """Is any surface form present as a word, not as a substring of another?

    Substring matching made `10` find `$100` and `mo` find `most`. The claim's
    value must actually appear. A mark that begins or ends with a symbol (`$`,
    `/mo`) is not bounded on that side, because the symbol *is* the boundary.
    """
    text = (text or "").casefold()
    for mark in marks:
        body = re.escape(mark)
        left = r"(?<![a-z0-9])" if mark[:1].isalnum() else ""
        right = r"(?![a-z0-9])" if mark[-1:].isalnum() else ""
        if re.search(left + body + right, text):
            return True
    return False


@dataclass(frozen=True)
class CompareResult:
    equivalent: bool
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:  # `if compare(...)` reads as "are they equal"
        return self.equivalent


def _amount(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except (TypeError, ValueError):
        return None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).strip(" .,;")


def _money(text: str) -> dict[str, Any] | None:
    """`$99/month`, `99 USD per month`, `$10 per member per month`.

    The tail is a list of qualifiers, not a fixed shape: a price can be per
    member, per month, or per member per month, and the order varies. An earlier
    version split on the literal word `per`, which turned `member per month`
    into an unknown token, failed the parse, and left the value as a string.
    A string claim value cannot use the currency/period scope checks, so the one
    comparison that matters most for pricing claims silently degraded to text
    equality — and `$10 per member per month` then looked like a contradiction of
    `$10 per member/month`.
    """
    t = _clean(text).lower()
    currency = None
    ambiguous = False
    for glyph, (code, amb) in _GLYPHS.items():
        if t.startswith(glyph.lower()):
            currency, ambiguous = code, amb
            t = t[len(glyph):].strip()
            break
    m = re.match(rf"^({_NUM})\s*([a-z]{{3}})?\s*(.*)$", t)
    if not m:
        return None
    amount_raw = m.group(1)
    code = (m.group(2) or "").strip()
    tail = (m.group(3) or "").strip()
    if code in {"per", "each", "the", "and"}:
        # The three-letter slot matched a connector, not a currency code.
        tail, code = f"{code} {tail}".strip(), ""
    amount = _amount(amount_raw)
    if amount is None:
        return None
    if currency is None and code:
        upper = code.upper()
        if upper in {c for c, _ in _GLYPHS.values()}:
            currency = upper
    if currency is None:
        return None

    # `billed`/`charged`/`paid` carry no value: "billed annually" and "annually"
    # state the same period.
    words = [w for w in re.split(r"[^a-z]+", tail)
             if w and w not in {"per", "each", "a", "an", "the", "and",
                                "billed", "charged", "paid"}]
    period = per_unit = None
    for word in words:
        # First mention wins: in "per member per month (billed annually)" the
        # price is monthly and the annual note is about how it is billed, so
        # letting the later word overwrite the period would invert the fact.
        if word in _PERIODS and period is None:
            period = _PERIODS[word]
        elif word in _UNITS and per_unit is None:
            per_unit = _UNITS[word]
        elif word not in _PERIODS and word not in _UNITS:
            return None
    return {"type": "money", "amount": amount, "currency": currency,
            "currency_ambiguous": ambiguous, "period": period,
            "per_unit": per_unit, "precision": len(amount_raw.split(".")[-1])
            if "." in amount_raw else 0}


def _percent(text: str) -> dict[str, Any] | None:
    t = _clean(text).lower()
    m = re.match(rf"^({_NUM})\s*(%|percent|percentage points?|pp)$", t)
    if not m:
        return None
    amount = _amount(m.group(1))
    if amount is None:
        return None
    basis = "percentage_points" if "point" in m.group(2) or m.group(2) == "pp" else "percent"
    return {"type": "percent", "amount": amount, "basis": basis}


def _version(text: str) -> dict[str, Any] | None:
    t = _clean(text).lower()
    # A bare integer is a number, not a version: `1200` and `1.200` must not
    # collapse into the same fact.
    m = re.match(r"^v(\d+(?:\.\d+){1,3})(?:[-+][\w.]+)?$", t)
    if not m:
        m = re.match(r"^(\d+(?:\.\d+){1,3})(?:[-+][\w.]+)?$", t)
    if not m:
        return None
    parts = m.group(1).split(".")
    return {"type": "version", "parts": parts, "scheme": "semver" if len(parts) >= 3 else "short"}


def _quantity(text: str) -> dict[str, Any] | None:
    t = _clean(text).lower().replace(",", "")
    m = re.match(rf"^({_NUM})\s*([a-z]+)$", t)
    if not m:
        return None
    unit = m.group(2)
    amount = _amount(m.group(1))
    if amount is None:
        return None
    key = unit[:-1] if unit.endswith("s") and (unit[:-1] in _DATA or unit[:-1] in _TIME) else unit
    if key in _DATA:
        return {"type": "quantity", "family": "data", "amount": amount,
                "unit": key, "base": amount * _DATA[key]}
    if key in _TIME:
        return {"type": "quantity", "family": "time", "amount": amount,
                "unit": key, "base": amount * _TIME[key]}
    return {"type": "quantity", "family": "unit", "amount": amount, "unit": key,
            "base": amount}


def _number(text: str) -> dict[str, Any] | None:
    t = _clean(text).replace(",", "")
    m = re.match(rf"^({_NUM})$", t)
    if not m:
        return None
    amount = _amount(m.group(1))
    if amount is None:
        return None
    return {"type": "number", "amount": amount, "precision": len(m.group(1).split(".")[-1])
            if "." in m.group(1) else 0}


def _date(text: str) -> dict[str, Any] | None:
    t = _clean(text)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", t)
    if m:
        return {"type": "date", "value": t, "precision": "day"}
    return None


def _duration(text: str) -> dict[str, Any] | None:
    t = _clean(text).lower()
    m = re.match(rf"^({_NUM})\s*(seconds?|minutes?|hours?|days?|weeks?|months?)$", t)
    if not m:
        return None
    amount = _amount(m.group(1))
    if amount is None:
        return None
    unit = m.group(2).rstrip("s")
    factor = {"second": 1, "minute": 60, "hour": 3600, "day": 86400,
              "week": 604800, "month": 2629800}[unit]
    return {"type": "duration", "amount": amount, "unit": unit,
            "seconds": amount * factor}


_PARSERS = (_money, _percent, _version, _quantity, _date, _duration, _number)


def normalize_value(raw: object) -> dict[str, Any]:
    """Turn a model/UI string into a typed, comparable shape.

    Unparseable input stays a string. It is not an error: `Acme` is a perfectly
    good value. It only means the comparison will be textual.
    """
    if isinstance(raw, dict) and raw.get("type"):
        return dict(raw)
    text = "" if raw is None else str(raw).strip()
    if not text:
        return {"type": "empty"}
    for parser in _PARSERS:
        parsed = parser(text)
        if parsed:
            return parsed
    return {"type": "string", "value": _clean(text).casefold()}


def _close(a: float, b: float, precision: int = 0) -> bool:
    if pref := max(precision, 0):
        return abs(a - b) < 0.5 * 10 ** (-pref)
    return abs(a - b) < 1e-9


def compare(left: object, right: object) -> CompareResult:
    """Do two values state the same fact?

    Returns a reason whenever the answer is "no" or "cannot tell". The caller
    must treat `equivalent=False` with a *scope* reason differently from a
    genuine different value: the first is an abstention, the second is a
    contradiction candidate.
    """
    a = normalize_value(left)
    b = normalize_value(right)
    ta, tb = a.get("type"), b.get("type")

    if ta != tb:
        # `99` and `$99` are not the same fact until a currency is known.
        return CompareResult(False, "type_mismatch", {"left": ta, "right": tb})
    if ta == "empty":
        return CompareResult(False, "empty_value")
    if ta == "string":
        return CompareResult(a["value"] == b["value"], "" if a["value"] == b["value"]
                             else "different_text")
    if ta == "money":
        # Scope is settled before amounts are compared. `$99/month` and
        # `€129/month` differ in both, but only the currency difference is the
        # finding: the numbers are not comparable across currencies, so calling
        # it `different_amount` would turn a scope mismatch into a rule-5
        # replacement and report `changed` for a claim nobody refuted.
        ac, bc = a.get("currency"), b.get("currency")
        if ac != bc:
            if a.get("currency_ambiguous") or b.get("currency_ambiguous"):
                return CompareResult(False, "ambiguous_currency",
                                     {"left": ac, "right": bc})
            return CompareResult(False, "different_currency",
                                 {"left": ac, "right": bc})
        if (a.get("period") or None) != (b.get("period") or None):
            return CompareResult(False, "different_period",
                                 {"left": a.get("period"), "right": b.get("period")})
        if (a.get("per_unit") or None) != (b.get("per_unit") or None):
            return CompareResult(False, "different_basis",
                                 {"left": a.get("per_unit"), "right": b.get("per_unit")})
        if not _close(float(a["amount"]), float(b["amount"]),
                      min(int(a.get("precision") or 0), int(b.get("precision") or 0))):
            return CompareResult(False, "different_amount")
        return CompareResult(True)
    if ta == "percent":
        if a.get("basis") != b.get("basis"):
            return CompareResult(False, "percent_vs_points",
                                 {"left": a.get("basis"), "right": b.get("basis")})
        ok = _close(float(a["amount"]), float(b["amount"]))
        return CompareResult(ok, "" if ok else "different_amount")
    if ta == "version":
        ok = a.get("parts") == b.get("parts")
        return CompareResult(ok, "" if ok else "different_version",
                             {"left": a.get("parts"), "right": b.get("parts")})
    if ta == "quantity":
        if a.get("family") != b.get("family"):
            return CompareResult(False, "different_unit_family")
        if a.get("family") == "unit":
            ok = str(a.get("unit", "")).rstrip("s") == str(b.get("unit", "")).rstrip("s") \
                and _close(float(a["amount"]), float(b["amount"]))
            return CompareResult(ok, "" if ok else "different_amount")
        # Same family: compare in base units. `10 GB` == `10,000 MB`.
        ok = _close(float(a["base"]), float(b["base"]), 0) or (
            abs(float(a["base"]) - float(b["base"])) / max(float(a["base"]), 1e-9) < 0.01)
        return CompareResult(ok, "" if ok else "different_amount")
    if ta == "duration":
        ok = _close(float(a["seconds"]), float(b["seconds"]), 0) or (
            abs(float(a["seconds"]) - float(b["seconds"])) / max(float(a["seconds"]), 1e-9) < 0.01)
        return CompareResult(ok, "" if ok else "different_duration")
    if ta == "number":
        precision = min(int(a.get("precision") or 0), int(b.get("precision") or 0))
        ok = _close(float(a["amount"]), float(b["amount"]), precision)
        return CompareResult(ok, "" if ok else "different_amount")
    if ta == "date":
        ok = a.get("value") == b.get("value")
        return CompareResult(ok, "" if ok else "different_date")
    return CompareResult(False, "unsupported_type", {"type": ta})


def differing_fields(left: object, right: object) -> dict[str, Any]:
    """A machine-readable diff for the UI, from the same normalisation."""
    a, b = normalize_value(left), normalize_value(right)
    keys = sorted(set(a) | set(b))
    return {k: {"from": a.get(k), "to": b.get(k)} for k in keys
            if k not in {"type"} and a.get(k) != b.get(k)}
