"""Deterministic sentence and proposition segmentation.

Extracts candidate propositions, numbers, currencies, dates, versions, and units
from raw text before calling model extraction, reducing prompt size and bounding hallucinations.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Regex patterns for deterministic entity and proposition detection
_CURRENCY_RE = re.compile(
    r"(?:\$|€|£|¥|₹|USD|EUR|GBP)\s*[\d,]+(?:\.\d+)?|\b[\d,]+(?:\.\d+)?\s*(?:USD|EUR|GBP|dollars?|cents?)\b",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:,\s*\d{4})?\b|"
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b|\b(?:in|since|as of|by)\s+(?:19\d\d|20\d\d)\b",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(r"\b(?:v|version)\s*\d+(?:\.\d+)+(?:-[a-z0-9.]+)?\b|\b\d+\.\d+\.\d+\b", re.IGNORECASE)
_RATE_UNIT_RE = re.compile(
    r"\b(?:requests?|tokens?|queries|calls)\s+(?:per|\/)\s*(?:minute|min|second|sec|hour|day|month)\b|"
    r"\b(?:RPM|TPM|QPS|RPS)\b|\b(?:per|\/)\s*(?:month|year|user|seat|seat\/month)\b",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?%", re.IGNORECASE)


@dataclass
class PropositionCandidate:
    sentence_index: int
    text: str
    char_start: int
    char_end: int
    currencies: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    versions: list[str] = field(default_factory=list)
    rate_units: list[str] = field(default_factory=list)
    percentages: list[str] = field(default_factory=list)
    has_numerical_fact: bool = False


def _split_into_sentences(text: str) -> list[tuple[str, int, int]]:
    """Split text into sentences with start and end character offsets."""
    sentences: list[tuple[str, int, int]] = []
    # Avoid splitting on abbreviations: Inc., Corp., Co., vs., e.g., i.e., etc.
    masked = re.sub(r"\b(Inc|Corp|Co|vs|e\.g|i\.e|Ltd|Dr|Mr|Ms)\.", r"\1<DOT>", text)
    # Avoid splitting on decimal numbers or version numbers like 4.2 or 3.11
    masked = re.sub(r"(\d+)\.(\d+)", r"\1<DOT>\2", masked)
    matches = list(re.finditer(r"[^.!?\n]+(?:[.!?]+|\n|$)", masked))

    for m in matches:
        raw_sentence = text[m.start():m.end()]
        clean = raw_sentence.strip()
        if clean:
            # Adjust offsets to stripped bounds
            offset_start = m.start() + (len(raw_sentence) - len(raw_sentence.lstrip()))
            offset_end = offset_start + len(clean)
            sentences.append((clean, offset_start, offset_end))

    return sentences


def segment_propositions(text: str) -> list[PropositionCandidate]:
    """Segment raw answer text into factual propositions with detected features."""
    sentences = _split_into_sentences(text)
    propositions: list[PropositionCandidate] = []

    for idx, (sent_text, start, end) in enumerate(sentences):
        currencies = _CURRENCY_RE.findall(sent_text)
        dates = _DATE_RE.findall(sent_text)
        versions = _VERSION_RE.findall(sent_text)
        rate_units = _RATE_UNIT_RE.findall(sent_text)
        percentages = _PERCENT_RE.findall(sent_text)

        has_fact = bool(currencies or dates or versions or rate_units or percentages)

        propositions.append(
            PropositionCandidate(
                sentence_index=idx,
                text=sent_text,
                char_start=start,
                char_end=end,
                currencies=currencies,
                dates=dates,
                versions=versions,
                rate_units=rate_units,
                percentages=percentages,
                has_numerical_fact=has_fact,
            )
        )

    return propositions
