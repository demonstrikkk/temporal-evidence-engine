"""The evidence gate, wired to the one function allowed to grant `verified`.

`validator.settle` was written for a dataset cell: a dict with a value, a
`verification_status` and a `source` carrying a quote. A claim is close enough to
a one-field record that it does not need a second gate — and adding one would be
the exact mistake the chokepoint test exists to prevent. So a claim assertion is
projected into a synthetic cell and settled by the same function the rest of the
substrate uses.

Two consequences are deliberate:

* A **failed support is not a contradiction.** If the quote does not settle the
  value, the honest reading is "this passage does not establish the claim", not
  "the claim is false". A contradiction must settle its *own* replacement value.
* `settle` returns the accepted quote sliced from stored text, together with its
  offsets. Those offsets are what the product shows a reader, so they are what
  gets stored — never a chunk-relative offset and never an offset into a reduced
  representation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services import validator
from app.verify.value_normalizer import CompareResult, compare, normalize_value


@dataclass(frozen=True)
class Settlement:
    ok: bool
    start: int | None
    end: int | None
    reason: str
    accepted_quote: str


def predicate_key(predicate: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", (predicate or "").strip().lower()).strip("_")
    return key or "value"


def synthetic_cell(subject: str, predicate: str, value_raw: Any) -> dict[str, Any]:
    """A one-field record. `settle` needs a cell, not a claim."""
    return {
        "subject": subject or "",
        predicate_key(predicate): "" if value_raw is None else str(value_raw),
        "verification_status": "unverified",
        "source": {},
    }


_CURRENCY_MARKS = {
    "USD": ("$", "us$", "usd"), "EUR": ("€", "eur"),
    "GBP": ("£", "gbp"), "INR": ("₹", "inr"),
}
_PERIOD_MARKS = {
    "month": ("month", "monthly", "/mo", "per mo", "mo."),
    "year": ("year", "annual", "yearly", "/yr", "per year"),
    "week": ("week", "weekly", "/wk"),
    "day": ("day", "daily", "/day"),
}
_UNIT_MARKS = {"seat": ("seat",), "user": ("user",), "employee": ("employee",)}


def _amount_marks(amount: float) -> list[str]:
    marks = {f"{amount:g}"}
    if amount == int(amount):
        marks.add(str(int(amount)))
        marks.add(f"{int(amount):,}")
    else:
        marks.add(f"{amount:.2f}")
    return [m.replace(",", "") for m in marks]


def evidence_speaks_to(value_raw: Any, quote: str) -> tuple[bool, str]:
    """Cheap deterministic check that the quote is *about* the stated value.

    `settle` guarantees the quote exists in the stored text; it does not and
    cannot know whether the passage talks about this value. Without this check a
    model that returns the company's address as "support" for its price would
    be accepted, because the address does resolve in the page.

    This is deliberately conservative: a value the quote does not mention at all
    is refused. It is not a semantic entailment engine and does not pretend to
    be one — the relationship proposal carries the semantics, and this keeps it
    honest.
    """
    if not quote:
        return False, "no quote"
    text = quote.casefold()
    flat = text.replace(",", "")
    value = normalize_value(value_raw)
    kind = value.get("type")

    if kind == "money":
        if not any(mark in flat for mark in _amount_marks(float(value["amount"]))):
            return False, "the quote does not contain the stated amount"
        marks = _CURRENCY_MARKS.get(str(value.get("currency")), ())
        if marks and not any(mark in text for mark in marks):
            return False, "the quote does not contain the stated currency"
        if value.get("period"):
            if not any(mark in text for mark in _PERIOD_MARKS.get(str(value["period"]), ())):
                return False, "the quote does not state the same billing period"
        if value.get("per_unit"):
            if not any(mark in text for mark in _UNIT_MARKS.get(str(value["per_unit"]), ())):
                return False, "the quote does not state the same per-unit basis"
        return True, ""

    if kind in {"number", "percent", "duration"}:
        if not any(mark in flat for mark in _amount_marks(float(value["amount"]))):
            return False, "the quote does not contain the stated amount"
        return True, ""

    if kind == "quantity":
        if not any(mark in flat for mark in _amount_marks(float(value["amount"]))):
            return False, "the quote does not contain the stated amount"
        unit = str(value.get("unit") or "")
        if unit and unit not in text:
            return False, "the quote does not state the same unit"
        return True, ""

    if kind == "version":
        joined = ".".join(value.get("parts") or [])
        if joined and joined not in text.replace("v", "v"):
            return False, "the quote does not contain the stated version"
        return True, ""

    if kind in {"date", "empty"}:
        return kind == "date" and str(value.get("value")) in text, ""

    # Textual value: require the value itself, or most of its words.
    words = [w for w in re.split(r"\W+", str(value.get("value") or "")) if len(w) >= 3]
    if not words:
        return True, ""
    hits = sum(1 for w in words if w in text)
    if str(value.get("value") or "") in text or hits / len(words) >= 0.6:
        return True, ""
    return False, "the quote does not mention the stated value"


def settle_assertion(*, subject: str, predicate: str, value_raw: Any,
                     quote: str, source_text: str) -> Settlement:
    """Did this passage establish this value, at a locatable position?"""
    if not quote or not source_text:
        return Settlement(False, None, None,
                          "no quote to settle against the stored source text", "")
    speaks, why = evidence_speaks_to(value_raw, quote)
    if not speaks:
        return Settlement(False, None, None, why, "")
    cell = synthetic_cell(subject, predicate, value_raw)
    settled = validator.settle(cell, status="verified", quote=quote,
                               source_text=source_text,
                               reason="the quote does not establish this value "
                                      "in the stored source text")
    ok = settled.get("verification_status") == "verified"
    src = settled.get("source") or {}
    return Settlement(
        ok=ok,
        start=src.get("start"),
        end=src.get("end"),
        reason="" if ok else str(settled.get("not_verified_because") or
                                 "the quote does not establish this value"),
        accepted_quote=str(src.get("quote") or ""),
    )


#: A comparison failure that means "this is a different scope", not "this is a
#: different fact". Both block `changed`; they must not become contradictions.
SCOPE_REASONS = {
    "scope_mismatch", "ambiguous_currency", "type_mismatch", "empty_value",
    "different_period", "different_basis", "different_unit_family",
    "percent_vs_points", "different_currency",
}


def scope_compatible(claim_value: Any, candidate_value: Any) -> CompareResult:
    """Compare the claim's value with a proposed replacement value.

    `equivalent=True` means the replacement does not replace anything.
    `equivalent=False` with a scope reason means we cannot tell (abstain).
    `equivalent=False` with a value reason means a real difference.
    """
    return compare(claim_value, candidate_value)


def is_scope_block(result: CompareResult) -> bool:
    return (not result.equivalent) and result.reason in SCOPE_REASONS
