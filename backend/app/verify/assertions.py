"""The second opinion that costs nothing.

Before a model is asked anything, the answer text itself is scanned for the
things a reader would check: money, percentages, measures, dates, versions and
bare quantities. This is deliberately not a claim extractor. It does not know
what a sentence means, so it cannot decide that something is true — it can only
notice that a sentence *asserts a value*, and a sentence that asserts a value
with no claim behind it is a gap in coverage.

That is the whole point. The extractor is allowed to return two claims from a
paragraph of eleven assertions; this module is what makes the other nine
countable, and the coverage audit is what makes them visible instead of silent.

Ordering matters and is enforced: the longest match at a position wins, so
`January 1887` is one date rather than a date plus a year, and `$129` is money
rather than a number. Everything here is regex over the original string, with
absolute offsets.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_MONTHS = (r"(?:January|February|March|April|May|June|July|August|September|"
           r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|"
           r"Oct|Nov|Dec)")

#: (kind, priority, pattern). Higher priority wins an overlap at the same start.
_PATTERNS: tuple[tuple[str, int, re.Pattern[str]], ...] = (
    ("date", 100, re.compile(
        r"\b\d{4}-\d{2}-\d{2}\b"
        rf"|\b\d{{1,2}}\s+{_MONTHS}\.?,?\s+\d{{4}}\b"
        rf"|\b{_MONTHS}\.?\s+(?:\d{{1,2}},?\s+)?\d{{4}}\b"
        r"|\bQ[1-4]\s+\d{4}\b", re.I)),
    ("money", 90, re.compile(
        r"(?:US\$|USD\s?|\$|\u20ac|\u00a3|\u20b9)\s?\d[\d,]*(?:\.\d+)?"
        r"(?:\s*(?:/|per)\s*[A-Za-z][\w -]{0,20})?")),
    ("percent", 80, re.compile(
        r"\b\d[\d,]*(?:\.\d+)?\s?(?:%|percent\b)")),
    ("measure", 70, re.compile(
        r"\b\d[\d,]*(?:\.\d+)?\s?[- ]?"
        r"(?:metric\s+)?(?:tonnes?|tons?|kilograms?|kg|grams?|g|"
        r"metres?|meters?|kilometres?|kilometers?|km|centimetres?|cm|\bm\b|"
        r"feet|foot|ft|miles?|mi|yards?|yd|inches|inch|in|"
        r"storeys?|stories|floors?|levels?|"
        r"seconds?|minutes?|hours?|days?|weeks?|months?|years?|decades?|"
        r"GB|MB|TB|KB|ms|kHz|MHz|GHz|kW|MW|GW|watts?|volts?)\b", re.I)),
    ("version", 60, re.compile(r"\bv?\d+\.\d+(?:\.\d+)*\b")),
    ("number", 50, re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"
                              r"|\b\d{2,}(?:\.\d+)?\b")),
)

_SIGNAL_PATTERNS = {
    "prediction": re.compile(
        r"\b(?:will|would|shall|likely|probably|perhaps|may|might|"
        r"expected to|projected|projections?|forecast|predicted|going to|"
        r"in the (?:coming|next) (?:years?|decades?|century|months?)|"
        r"by (?:19|20)\d{2})\b", re.I),
    "subjective": re.compile(
        r"\b(?:consider(?:s|ed)?|believ(?:e|es|ed)|feels?|felt|think|thought|"
        r"opinion|arguably|beautiful|stunning|romantic|amazing|iconic|"
        r"legendary|must-see|beloved|favourite|favorite|charming|"
        r"impressive|breathtaking|iconic|famous|popular)\b", re.I),
    "attribution": re.compile(
        r"\b(?:according to|reported by|survey|surveys|poll|polls|study|studies|"
        r"research(?:ers)?|experts?|critics?|reviewers?|awards?|ranked|"
        r"ranking|data (?:from|shows)|statistics?)\b", re.I),
    "comparative": re.compile(
        r"\b(?:tallest|highest|largest|biggest|smallest|oldest|newest|"
        r"longest|shortest|fastest|slowest|first|last|most|least|best|worst|"
        r"more than|less than|fewer|greater|higher|lower)\b", re.I),
    "approximate": re.compile(
        r"\b(?:roughly|approximately|about|around|nearly|almost|circa|"
        r"some|an estimated|up to|as many as)\b", re.I),
}

_DISPOSITION_HINTS = {
    "subjective": "subjective",
    "attribution": "subjective",
    "prediction": "prediction",
}


@dataclass(frozen=True)
class Candidate:
    """One value a reader could check, at an absolute offset in the answer."""

    kind: str
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class Signals:
    """Sentence-level reading. Never a verdict — only why a gap may be excused."""

    prediction: bool = False
    subjective: bool = False
    attribution: bool = False
    comparative: bool = False
    approximate: bool = False

    def dispositions(self) -> list[str]:
        out = []
        if self.subjective or self.attribution:
            out.append("subjective")
        if self.prediction:
            out.append("prediction")
        return out

    def as_dict(self) -> dict[str, bool]:
        return {
            "prediction": self.prediction,
            "subjective": self.subjective,
            "attribution": self.attribution,
            "comparative": self.comparative,
            "approximate": self.approximate,
        }


def signals(sentence: str) -> Signals:
    """Read the sentence's own words. No model, no inference beyond the regexes."""
    return Signals(
        prediction=bool(_SIGNAL_PATTERNS["prediction"].search(sentence)),
        subjective=bool(_SIGNAL_PATTERNS["subjective"].search(sentence)),
        attribution=bool(_SIGNAL_PATTERNS["attribution"].search(sentence)),
        comparative=bool(_SIGNAL_PATTERNS["comparative"].search(sentence)),
        approximate=bool(_SIGNAL_PATTERNS["approximate"].search(sentence)),
    )


def candidates(text: str) -> list[Candidate]:
    """Every checkable value in `text`, non-overlapping, in document order."""
    found: list[tuple[int, int, int, str, str]] = []
    for kind, priority, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            if not value.strip():
                continue
            found.append((match.start(), match.end(), priority, kind, value))
    # Longest wins, then priority, then leftmost. The sort key is written this
    # way so a date containing a year is not split into two assertions.
    found.sort(key=lambda item: (-(item[1] - item[0]), -item[2], item[0]))
    accepted: list[Candidate] = []
    taken: list[tuple[int, int]] = []
    for start, end, _priority, kind, value in found:
        if any(start < t_end and end > t_start for t_start, t_end in taken):
            continue
        taken.append((start, end))
        accepted.append(Candidate(kind=kind, text=value.strip(),
                                  start=start, end=end))
    accepted.sort(key=lambda c: c.start)
    return accepted


def sentence_candidates(start: int, end: int, text: str) -> list[Candidate]:
    """Candidates inside one sentence, rebased to absolute answer offsets."""
    return [Candidate(kind=c.kind, text=c.text, start=start + c.start,
                      end=start + c.end)
            for c in candidates(text)]


def disposition_for(text: str, *, covered: bool = False) -> str:
    """`checked` when a claim covers it, otherwise the best honest excuse.

    `subjective` and `prediction` are not verification outcomes; they say the
    sentence asserts something a source cannot settle. `not_checked` is the
    admission that nothing covered it and nothing excuses it. The caller decides
    between the excuse and the admission by passing `covered`.
    """
    if covered:
        return "checked"
    read = signals(text)
    for hint in _DISPOSITION_HINTS:
        if getattr(read, hint):
            return _DISPOSITION_HINTS[hint]
    return "not_checked"


@dataclass
class CandidateTally:
    by_kind: dict[str, int] = field(default_factory=dict)
    total: int = 0

    def add(self, kind: str) -> None:
        self.by_kind[kind] = self.by_kind.get(kind, 0) + 1
        self.total += 1
