"""Build the stored document text, then take every field's quote from it.

The corpus stores a small metadata header followed by the source's own body.
The header is not decoration: it is how a curated value such as a citation or a
court name gets a quote that resolves. The header only repeats values already in
`sources.json`; the body is exactly what the source returned.

Every field quote is a slice of the stored text, not a string assembled for the
dataset. `quote_for` returns the original characters at the located span, so a
reader slicing `[start:end]` sees the same words.
"""
from __future__ import annotations

import re

_WS = re.compile(r"\s+")


def clean(value: object) -> str:
    return _WS.sub(" ", str(value or "")).strip()


def _norm_with_map(src: str) -> tuple[str, list[int]]:
    out: list[str] = []
    idx: list[int] = []
    prev_space = True
    for i, ch in enumerate(src):
        if ch.isspace():
            if not prev_space:
                out.append(" ")
                idx.append(i)
            prev_space = True
        else:
            out.append(ch.lower())
            idx.append(i)
            prev_space = False
    return "".join(out), idx


def find_span(text: str, needle: str) -> tuple[int, int] | None:
    """Locate `needle` in `text`, returning offsets into the original text."""
    text = text or ""
    needle = needle or ""
    if not text or not needle:
        return None
    start = text.find(needle)
    if start >= 0:
        return start, start + len(needle)
    low = text.lower().find(needle.lower())
    if low >= 0:
        return low, low + len(needle)
    normalized, index = _norm_with_map(text)
    wanted = _WS.sub(" ", needle.strip()).lower()
    at = normalized.find(wanted)
    if at < 0 or not index:
        return None
    end = min(at + len(wanted) - 1, len(index) - 1)
    return index[at], index[end] + 1


def quote_for(text: str, needle: str) -> str:
    span = find_span(text, needle)
    return text[span[0]:span[1]] if span else ""


def header_lines(document: dict) -> dict[str, str]:
    """The metadata lines the stored text begins with, keyed by field name."""
    title = clean(document.get("title"))
    doc_type = clean(document.get("doc_type"))
    citation = clean(document.get("citation"))
    court = clean(document.get("court"))
    jurisdiction = clean(document.get("jurisdiction"))
    year = clean(document.get("year"))
    act = clean(document.get("act"))
    section = clean(document.get("section"))
    values = {
        "document_title": title,
        "document_type": doc_type,
        "citation": citation,
        "court": court,
        "jurisdiction": jurisdiction,
        "year": year,
        "act": act,
        "section": section,
    }
    labels = {
        "document_title": "Title",
        "document_type": "Type",
        "citation": "Citation",
        "court": "Court",
        "jurisdiction": "Jurisdiction",
        "year": "Year",
        "act": "Act",
        "section": "Section",
    }
    return {name: f"{labels[name]}: {value}"
            for name, value in values.items() if value}


def compose(document: dict, body: str) -> tuple[str, dict[str, str]]:
    """Header + body, and the exact header line for each field."""
    lines = header_lines(document)
    header = "\n".join(lines.values())
    text = f"{header}\n\n{body}".strip()
    return text, lines


def _trim(candidate: str, *, min_chars: int, max_chars: int) -> str:
    candidate = candidate.strip()
    if len(candidate) <= max_chars:
        return candidate
    cut = candidate[:max_chars]
    stop = max(cut.rfind(". "), cut.rfind("; "), cut.rfind("\n"))
    if stop >= min_chars:
        cut = cut[:stop + 1]
    return cut.strip()


def _looks_like_front_matter(line: str) -> bool:
    low = " ".join(line.strip().lower().split())
    if low.startswith("page ") or "page " in low[:12]:
        return True
    if re.search(r"\(\d+\s+of\s+\d+\)", line):
        return True
    if re.match(r"^\[\d{4}/", line):
        return True
    if sum(1 for ch in line if ch.isupper()) > max(8, len(line) * 0.65):
        return True
    if low.startswith(("civil appellate", "criminal appellate",
                      "special leave petition", "from the judgment",
                      "criminal review application", "in person for the")):
        return True
    if re.search(r"\bfor the (appellant|respondent|petitioner|state)\b", low):
        return True
    if low.endswith((" for the", " for")):
        return True
    return any(marker in low for marker in (
        "in the supreme court", "in the high court", "high court of judicature",
        "supreme court of india", "high court", "criminal appeal no",
        "writ petition no", "letters patent appeal", "rfa(", "ba appl",
        "criminal misc", "s.b. civil", "l.p.a", "versus",
        "through:", "advocate", "from the judgment", "delivered by",
        "the judgment of", "for the appellant", "for the respondent",
        "for the state", "for the petitioners", "special leave petition",
        "appellate jurisdiction", "supreme court reports")) or bool(
            re.search(r"\wv\.", line))


def summary_quote(body: str, *, min_chars: int = 120, max_chars: int = 900) -> str:
    """The first substantive paragraph, as an exact slice of `body`.

    A judgment PDF starts with a caption, not a holding. Taking the first long
    block made "Page 1 / REPORTABLE / IN THE SUPREME COURT..." the summary of
    every Supreme Court case. When a judgment marker exists, start after it and
    skip the author/caption lines; otherwise fall back to the first paragraph
    that does not look like page furniture.
    """
    body = (body or "").strip()
    if not body:
        return ""
    for marker in ("J U D G M E N T", "JUDGMENT", "Headnotes",
                   "Issue for Consideration", "ORDER"):
        # "DATE OF JUDGMENT" appears in the metadata block before the real
        # heading. Taking the last occurrence skips it; ORDER is the one marker
        # whose operative text is at the end, so it keeps the first hit.
        at = body.find(marker) if marker == "ORDER" else body.rfind(marker)
        if at < 0:
            continue
        tail = body[at + len(marker):]
        for match in re.finditer(r"(?m)^[ \t]*(.+)$", tail):  # noqa: E501
            candidate = match.group(1).strip()
            if len(candidate) < 30 or len(candidate.split()) < 4:
                continue
            if candidate[0].islower():
                continue
            if _looks_like_front_matter(candidate):
                continue
            start = match.start(1)
            return _trim(tail[start:], min_chars=min_chars,
                         max_chars=max_chars)

    chunks = re.split(r"\n\s*\n", body)
    if len(chunks) == 1:
        chunks = [line for line in body.splitlines() if line.strip()]
    for chunk in chunks:
        candidate = chunk.strip()
        if len(candidate) < min_chars or _looks_like_front_matter(candidate):
            continue
        return _trim(candidate, min_chars=min_chars, max_chars=max_chars)

    # No blank-line paragraph survived (JUDIS PDFs often have none). Walk the
    # lines instead; the first substantive line is a slice point, and the text
    # after it still contains the rest of the paragraph.
    for match in re.finditer(r"(?m)^[ \t]*(.+)$", body):
        candidate = match.group(1).strip()
        if len(candidate) < 40 or len(candidate.split()) < 6:
            continue
        if _looks_like_front_matter(candidate):
            continue
        return _trim(body[match.start(1):], min_chars=min_chars,
                     max_chars=max_chars)
    return _trim(body[:max_chars], min_chars=min_chars, max_chars=max_chars)


def fields_for(document: dict, text: str, lines: dict[str, str],
               body: str) -> dict[str, dict]:
    """Every field the document can support, with its exact quote."""
    fields: dict[str, dict] = {}
    for name, line in lines.items():
        quote = quote_for(text, line)
        if not quote:
            continue
        raw = line.split(": ", 1)[1] if ": " in line else line
        if name == "year":
            try:
                value: object = int(raw)
            except ValueError:
                value = raw
        else:
            value = raw
        fields[name] = {
            "value": value,
            "quote": quote,
            "read_method": "api" if str(document.get("content_type", "")).find("json") >= 0
                           else "regex",
        }

    summary = summary_quote(body)
    if summary:
        quote = quote_for(text, summary)
        if quote:
            fields["summary"] = {"value": quote, "quote": quote,
                                 "read_method": "regex"}

    eligible = dlsa_quote(body)
    if eligible:
        fields["dlsa_eligible"] = {"value": True, "quote": eligible,
                                   "read_method": "regex"}
    return fields


def dlsa_quote(body: str) -> str:
    """A sentence that explicitly ties eligibility to free legal aid, or "".

    The field is positive-only by design. A judgment about legal aid does not
    make its parties eligible, so this looks for the two ideas in one sentence
    rather than for the topic alone.
    """
    for sentence in re.split(r"(?<=[.!?])\s+", body or ""):
        low = sentence.lower()
        if ("eligible" in low or "entitled" in low) and (
                "free legal aid" in low or "legal services" in low
                or "legal aid" in low):
            return sentence.strip()
    return ""
