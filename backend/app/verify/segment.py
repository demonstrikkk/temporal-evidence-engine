"""Sentence boundaries over the answer's own text, with offsets that survive.

Everything in the coverage audit hangs off this: if a sentence boundary is wrong,
an assertion is attributed to the wrong span, and the audit reports a gap in a
place where nothing was written. It is therefore deterministic, it never calls a
model, and it indexes into the **original** string — the same string the quote
locator and the stored source revisions use.

What counts as a boundary:

* `.` `!` `?` followed by whitespace and something that can start a sentence;
* a blank line or a bullet/number marker at the start of a line;
* never an abbreviation (`Dr.`, `e.g.`, `U.S.`), an initial (`J. Smith`), a
  decimal (`3.5`) or a version (`v1.2`).

The test suite pins each of those, because every one of them was a real
mis-split in a draft of this file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: Abbreviations whose trailing dot is not a sentence end. Lowercased, without
#: the dot. A token ending in one of these is joined to what follows.
_ABBREVIATIONS: frozenset[str] = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "ft",
    "no", "vs", "etc", "al", "approx", "est", "fig", "ref", "sec",
    "inc", "ltd", "co", "corp", "llc", "plc", "dept", "univ",
    "e.g", "i.e", "cf", "viz", "esp", "incl", "excl",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec",
})

_TERMINATOR = re.compile(r"[.!?]+")
_OPENING = re.compile(r"[\"'(\[\u201c\u2018]")
_LINE_MARKER = re.compile(r"(?m)^[ \t]*(?:[-*\u2022]|\d{1,2}[.)])[ \t]+")
#: A word token ending at the terminator, used to test the abbreviation set.
_WORD_BEFORE = re.compile(r"([A-Za-z][A-Za-z.\u2019']*)$")
#: Two or more terminal newlines always end a block.
_BLANK_LINE = re.compile(r"\n[ \t]*\n")


@dataclass(frozen=True)
class Sentence:
    """One sentence, with offsets into the answer it came from."""

    start: int
    end: int
    text: str

    def contains(self, offset: int) -> bool:
        return self.start <= offset < self.end

    def overlaps(self, start: int, end: int) -> bool:
        """True when `[start, end)` shares any character with this sentence."""
        if start < 0 or end <= start:
            return False
        return start < self.end and end > self.start


def _is_abbreviation(before: str) -> bool:
    match = _WORD_BEFORE.search(before)
    if not match:
        return False
    token = match.group(1).lower().replace("\u2019", "")
    # A single letter is an initial: "J. Smith", "A. Merkel".
    if len(token) == 1:
        return True
    return token in _ABBREVIATIONS


def _starts_sentence(after: str) -> bool:
    """Is the text following the terminator able to begin a sentence?"""
    probe = after.lstrip()
    if not probe:
        return False
    head = probe[0]
    if _OPENING.match(head):
        probe = probe[1:].lstrip()
        return bool(probe) and (probe[0].isupper() or probe[0].isdigit())
    return head.isupper() or head.isdigit() or head in "-*\u2022>#"


def _split_points(text: str) -> list[int]:
    """Absolute offsets where a new sentence begins."""
    points: list[int] = []
    for match in _TERMINATOR.finditer(text):
        end = match.end()
        # A decimal or a version number: "3.5", "v1.2.3", "1.2".
        if end < len(text) and text[end].isdigit():
            continue
        if end == len(text):
            continue
        after = text[end:]
        if not after[:1].isspace():
            continue
        if _is_abbreviation(text[:match.start()]):
            continue
        if not _starts_sentence(after):
            continue
        points.append(end)
    for match in _BLANK_LINE.finditer(text):
        points.append(match.end())
    for match in _LINE_MARKER.finditer(text):
        points.append(match.start())
    return sorted(set(points))


def segment(text: str) -> list[Sentence]:
    """Split `text` into sentences, preserving offsets and never losing a char.

    Every non-whitespace character of the input belongs to exactly one returned
    sentence. That is the property the coverage audit relies on: "nothing was
    skipped" is only checkable if the segmentation itself cannot skip.
    """
    if not text:
        return []
    sentences: list[Sentence] = []
    cursor = 0
    for point in _split_points(text) + [len(text)]:
        if point <= cursor:
            continue
        raw = text[cursor:point]
        start, end = _trim(raw, cursor)
        if end > start:
            sentences.append(Sentence(start=start, end=end,
                                      text=text[start:end]))
        cursor = point
    return sentences


def _trim(raw: str, base: int) -> tuple[int, int]:
    left = 0
    while left < len(raw) and raw[left].isspace():
        left += 1
    right = len(raw)
    while right > left and raw[right - 1].isspace():
        right -= 1
    return base + left, base + right


def coverage_of(sentences: list[Sentence]) -> int:
    """Total characters covered. Used by the audit's own self-check."""
    return sum(sentence.end - sentence.start for sentence in sentences)
