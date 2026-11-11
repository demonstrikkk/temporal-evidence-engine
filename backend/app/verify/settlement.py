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
from app.verify.value_normalizer import (CURRENCY_MARKS, PERIOD_MARKS,
                                         UNIT_MARKS, compare, mentions,
                                         normalize_value)


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


#: Words that describe a plan or a page rather than the thing being claimed. A
#: subject is anchored by its last *distinctive* word, because "Notion Plus" and
#: "Notion Business" are the same brand and different facts.
_GENERIC_SUBJECT_WORDS = {
    "the", "a", "an", "of", "and", "for", "to", "in", "on", "at", "with",
    "plan", "plans", "tier", "tiers", "price", "pricing", "cost", "costs",
    "subscription", "subscriptions", "service", "services", "product",
    "products", "platform", "app", "apps", "software", "tool", "tools",
    "company", "companies", "team", "teams", "edition", "editions", "level",
}

#: Same word, different register. Only aliases that a reader would accept as
#: naming the same thing; no speculative synonyms.
_QUALIFIER_ALIASES = {
    "pro": ("pro", "professional"),
    "plus": ("plus",),
    "enterprise": ("enterprise", "enterprises"),
    "business": ("business",),
    "free": ("free",),
    "student": ("student", "students"),
    "teacher": ("teacher", "teachers"),
}


def _amount_marks(amount: float) -> list[str]:
    marks = {f"{amount:g}"}
    if amount == int(amount):
        marks.add(str(int(amount)))
        marks.add(f"{int(amount):,}")
    else:
        marks.add(f"{amount:.2f}")
    return [m.replace(",", "") for m in marks]


def _states_amount(amount: float, text: str) -> bool:
    """The quote states this number, not a longer one that contains its digits."""
    return mentions(text.replace(",", ""), _amount_marks(amount))


def _is_statement(quote: str) -> tuple[bool, str]:
    """Is this a sentence a reader could check, or just a fragment?

    A page's footnote marker is a perfectly locatable string: `[2013]` exists in
    the stored text at an exact offset, and `settle` will happily verify it. It
    is not evidence. A live run produced exactly that — `[2013]` was returned as
    the quote for a contradiction of "Notion was founded in 2013", the number in
    the marker matched the claim's value, and a citation index became a
    refutation of a true fact.

    So the gate refuses anything without enough words to be a statement. This is
    a floor, not an entailment check: three words can still be wrong, but a
    bracket, a bare number, a date, or a heading fragment cannot be right.
    """
    words = re.findall(r"[A-Za-z][A-Za-z'\-]+", quote or "")
    if len(words) >= 3:
        return True, ""
    return False, "the quote is not a statement about the claim"


def _subject_forms(subject: str) -> tuple[str, ...]:
    words = re.findall(r"[a-z0-9]+", (subject or "").casefold())
    distinctive = [w for w in words if len(w) >= 2 and w not in _GENERIC_SUBJECT_WORDS]
    if not distinctive:
        return ()
    qualifier = distinctive[-1]
    return _QUALIFIER_ALIASES.get(qualifier, (qualifier, qualifier + "s"))


def subject_named(quote: str, subject: str) -> tuple[bool, str]:
    """Does the passage name the part of the subject that makes it this fact?

    A contradiction asserts that a claim is false, so it has to be about the
    same thing. The model's own proposal is not enough: in a live run, "Business
    costs $24 monthly" was proposed — and settled — as a refutation of "Notion
    Plus costs $10 per member per month". The value check passed because $24 is
    in the quote; the subject was never checked, so a statement about a
    *different plan* became a contradiction of a claim four sources supported.

    The qualifier is required rather than the brand, because a passage may drop
    the brand ("The Plus plan costs $10...") on a page that is entirely about it,
    while dropping the qualifier is exactly what makes the sentence about a
    different fact. Matching is on word boundaries: `pro` must not be satisfied
    by `product`.
    """
    forms = _subject_forms(subject)
    if not forms:
        return True, ""
    text = (quote or "").casefold()
    for form in forms:
        if re.search(rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9])", text):
            return True, ""
    return False, (f"the quote does not mention the claim's subject "
                   f"({forms[0]})")


def object_terms_named(quote: str, object_text: str) -> tuple[bool, str]:
    """Is the passage about the same *property* the claim states?

    Naming the subject is not enough when the claim has no numeric value to
    compare. Measured on a live run: "The Notion free plan lets an individual
    store unlimited pages and blocks" was refuted by "Free plans have a 5 MB
    file upload limit per file". The subject ("free plan") is named, the quote
    resolves, and settlement accepted it -- but a file-size cap says nothing
    about a page/block allowance, so it is not a refutation of this claim.

    Applied only when the claim's value is textual. A money, quantity, period or
    version is already compared field by field, and `compare` is the better
    judge there; running this check on top would reject a genuine refutation
    that phrases the disagreement differently ("headquartered in Munich" for a
    claim of "located in Berlin").

    A quote that shares no distinctive term is refused; an object with no
    distinctive terms at all is not checked.
    """
    if normalize_value(object_text).get("type") != "string":
        return True, ""
    terms = [word for word in re.findall(r"[a-z0-9]+", (object_text or "").casefold())
             if len(word) >= 3 and word not in _GENERIC_SUBJECT_WORDS]
    if not terms:
        return True, ""
    text = (quote or "").casefold()
    for term in terms:
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text):
            return True, ""
    return False, ("the quote is about a different property: it mentions none "
                   f"of {', '.join(terms[:4])}")


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
    statement, why = _is_statement(quote)
    if not statement:
        return False, why
    text = quote.casefold()
    value = normalize_value(value_raw)
    kind = value.get("type")

    if kind == "money":
        if not _states_amount(float(value["amount"]), text):
            return False, "the quote does not contain the stated amount"
        marks = CURRENCY_MARKS.get(str(value.get("currency")), ())
        if marks and not mentions(text, marks):
            return False, "the quote does not contain the stated currency"
        if value.get("period"):
            if not mentions(text, PERIOD_MARKS.get(str(value["period"]), ())):
                return False, "the quote does not state the same billing period"
        if value.get("per_unit"):
            if not mentions(text, UNIT_MARKS.get(str(value["per_unit"]), ())):
                return False, "the quote does not state the same per-unit basis"
        return True, ""

    if kind in {"number", "percent", "duration"}:
        if not _states_amount(float(value["amount"]), text):
            return False, "the quote does not contain the stated amount"
        return True, ""

    if kind == "quantity":
        if not _states_amount(float(value["amount"]), text):
            return False, "the quote does not contain the stated amount"
        unit = str(value.get("unit") or "")
        if unit and not mentions(text, (unit, unit + "s")):
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
                     quote: str, source_text: str,
                     must_name_subject: bool = False,
                     object_text: str = "") -> Settlement:
    """Did this passage establish this value, at a locatable position?

    `must_name_subject` is set by the caller for a proposal that *contradicts*:
    refuting a claim requires naming the same subject, which a support does not
    (a supporting sentence may say "it costs $10" in a paragraph that already
    named the subject). See `subject_named` for the defect this prevents.

    `object_text` is the claim's own value; when it is supplied, a contradiction
    must also be about that property. See `object_terms_named`.
    """
    if not quote or not source_text:
        return Settlement(False, None, None,
                          "no quote to settle against the stored source text", "")
    speaks, why = evidence_speaks_to(value_raw, quote)
    if not speaks:
        return Settlement(False, None, None, why, "")
    if must_name_subject:
        named, why = subject_named(quote, subject)
        if not named:
            return Settlement(False, None, None, why, "")
        if object_text:
            about, why = object_terms_named(quote, object_text)
            if not about:
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
