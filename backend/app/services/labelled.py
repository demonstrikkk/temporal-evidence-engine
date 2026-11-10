"""Fields written as `Label: value` on the page, read without a model.

Most listing pages are not tables. They are a heading per record followed by a
few labelled lines, and that is the shape this workspace's pages actually have —
34 companies on one page, each introduced by a heading and described by lines like

    ## 1. Mistral
    Industry: Artificial Intelligence / Generative AI
    Location: Paris, Île-de-France, France
    Founded: 2023
    Last Funding Round: €1.7B Series C

A page that states its own fields in this form does not need a language model to
be read, and asking one costs a call, latency and a chance to misread a value the
page already printed. Measured over a stored page: **8 field labels × 34 records,
zero model calls**, against a ladder whose DOM rung cannot fire at all without a
checked-in schema.

This rung needs no per-domain schema file, which is what makes it the practical
one. The alternative — a checked-in selector schema per site — is precise but
covers one domain per file, and this workspace has exactly one such file.

## Why a label match is trusted at all

Because the match is anchored on both sides: the line must *begin* with a
`Label:` and the label must resolve to a field the plan actually asked for. A
field is only filled when the page names it, not when the text happens to contain
the word — so this cannot scrape an incidental mention into a column.

Labels are compared by normalised form (`Last Funding Round` → `funding round`)
and then by token overlap with the field's name and description, which is how
`Last Funding Round` answers `funding_stage` and `Industry` answers `industry`
without either being renamed to suit the other.

## It is a first rung, not an authority

Values found here are stamped `read_method: "labelled"` and then pass the same
gate as everything else. The route changes where a value came from; it never
changes whether it was checked. A label match is a strong hint that the page
states the field, and the quote check is what confirms it.
"""
from __future__ import annotations

import re

from app.services import confidence as conf_svc

#: `Label: value`, anchored at the start of a line. The label may not contain a
#: colon (no nesting, no times like 12:30) and the value must be non-empty.
#:
#: Reducers wrap labels in emphasis — `**Industry:** AI` is what a bold table
#: field becomes in markdown — so one layer of `*`, `_` or backtick is allowed on
#: either side of the colon. It is matched, not stripped: the stored quote has to
#: stay the page's original line so it still locates.
_LABELLED = re.compile(
    r"^(?:\*\*|__|[*_`])?"
    r"([A-Z][A-Za-z0-9][A-Za-z0-9 /&_'-]{1,38}?)"
    r"(?:\*\*|__|[*_`])?\s*:\s*(?:\*\*|__|[*_`])?\s*(\S.*)$"
)

#: A heading that introduces a record, e.g. `## 1. Mistral` or `### Anthropic`.
_HEADING = re.compile(r"^(#{1,6})\s+(?:[\d]+[.)]\s*)?(.+?)\s*$")

#: Markdown furniture a reducer adds in front of a label: a list bullet or an
#: ordered-list number. The page still says `Industry: AI`; the marker is the
#: renderer's, not the source's.
_BULLET = re.compile(r"^(?:[•·]\s+|[-*+]\s+|\d{1,3}[.)]\s+)")

#: Emphasis markers around a label, e.g. `**Industry:** AI`. Stripped for the
#: match only; the stored quote stays the original line so it still locates.
_EMPHASIS = re.compile(r"\*\*|__|`")

#: Field names whose value is a heading rather than a labelled line, so the
#: heading becomes the value instead of being ignored.
_NAMEISH = ("company_name", "company", "ngo_name", "startup_name", "entity_name",
            "organization", "organisation", "employer")

#: How many tokens of overlap before a label is considered to name a field. Two is
#: the floor because one shared word is not a match: `Location` should not answer
#: `valuation`, and `Funding` should not answer `founded`.
_MIN_TOKEN_OVERLAP = 2

#: The longest labelled value this rung will store.
#:
#: A labelled value is one line of the page, and the line is also the quote, so
#: there is no structural need for a tight bound. The old limit of 160 dropped
#: addresses, funding rounds and short descriptions that the page stated
#: outright, which then went to the model — the opposite of the rung's purpose.
#: 500 is long enough for a postal address or a sentence and short enough that a
#: mis-anchored paragraph cannot become a cell.
_MAX_VALUE_CHARS = 500


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split(" ") if t}


def _stop(tok: str) -> bool:
    """Tokens too common to carry meaning in a label match."""
    return tok in {"the", "of", "and", "a", "an", "to", "for", "in", "on", "is",
                   "or", "by", "at", "no", "per", "as", "its", "it"}


def _label_matches(label: str, spec: dict) -> bool:
    """Whether a page's label names the field the plan asked for.

    Three passes, strongest first: an exact normalised match, then a prefix match
    either way (`Industry` against `industry_segment`), then token overlap across
    the field's name *and* its description. The description matters because a plan
    writes `funding_stage` with the description "Funding stage / amount", and a
    page that prints "Last Funding Round" is answering it.
    """
    lab = _norm(label)
    name = _norm(spec.get("name") or "")
    desc = _norm(spec.get("description") or "")
    if not lab:
        return False
    if lab == name or (name and (lab.startswith(name) or name.startswith(lab))):
        return True

    lab_tokens = {t for t in _tokens(label) if not _stop(t)}
    if not lab_tokens:
        return False
    for pool in (_tokens(spec.get("name")), _tokens(desc)):
        overlap = len(lab_tokens & {t for t in pool if not _stop(t)})
        if overlap >= _MIN_TOKEN_OVERLAP:
            return True
    return False


def _clean_value(raw: str) -> str:
    """Trim a labelled value to the sentence it states.

    Trailing sentence punctuation is dropped because it is the page's prose
    rather than part of the value, and markdown emphasis left by the reducer is
    stripped so a bold label does not leak into the cell.
    """
    v = _EMPHASIS.sub("", str(raw or "")).strip()
    v = re.sub(r"\s+", " ", v)
    return v.rstrip(" .;,·—-")


def _label_match(line: str):
    """Match `Label: value` through the markdown a reducer adds.

    A list bullet is renderer furniture, not part of the label, so it is removed
    before matching. The original line is what gets quoted.
    """
    return _LABELLED.match(_BULLET.sub("", line.strip()))


def labelled_pairs(markdown: str, wanted: list[dict]) -> dict[str, tuple[str, str]]:
    """The **first record's** fields, as `field -> (value, quote)`.

    One record, and that matters more than it looks. Resetting at every heading
    would return the *last* record's values while the name came from the first,
    because a name is written once at a heading and every other field is written
    per record — so a page would yield `company_name = "Mistral"` beside
    `location = "London"` from a different company entirely. That is worse than
    extracting nothing: every value is individually correct and the row is a
    fiction.

    So the walk stops at the end of the first record. A page with thirty records
    yields the first one here; the container rung is what splits a listing page
    into rows, and until a schema exists for this shape it is honest to return one
    correct record rather than thirty spliced ones.

    The record is located by content rather than by "whatever follows the first
    heading": listing pages usually carry a page title (`# Top AI Companies`)
    above the records (`## Mistral`), and reading the title's section returned the
    intro and no fields at all. The first section that prints a recognised label
    wins, and a heading supplies the entity name only when its level repeats,
    which is what makes it a listing rather than a page title or a section label.

    Values that are really absences are skipped. `Valuation: Not publicly
    disclosed` states that the page has no valuation, and storing it would turn
    an absent field into a string a reader has to know to distrust.

    The quote is the line the page printed, in its original markdown form; the
    value is that line's cleaned value. A downstream check that the quote is on
    the stored page is therefore checking that this line really came from there.
    """
    if not wanted:
        return {}
    by_spec = [s for s in wanted if isinstance(s, dict) and s.get("name")]
    if not by_spec:
        return {}

    from app.services.normalizer import is_placeholder

    lines = [raw.strip() for raw in (markdown or "").split("\n")]
    heads: list[tuple[int, int, str, str]] = []
    for i, line in enumerate(lines):
        head = _HEADING.match(line)
        if head:
            heads.append((i, len(head.group(1)), _clean_value(head.group(2)), line))
    if not heads:
        # Nothing before the first heading belongs to a record this rung can
        # attribute a value to.
        return {}

    def _usable(label: str, value: str) -> bool:
        return (bool(value) and len(value) <= _MAX_VALUE_CHARS
                and not is_placeholder(value)
                and any(_label_matches(label, s) for s in by_spec))

    def _opens_a_record(section: list[str]) -> bool:
        for line in section:
            m = _label_match(line)
            if m and _usable(m.group(1).strip(), _clean_value(m.group(2))):
                return True
        return False

    level_count: dict[int, int] = {}
    for _i, level, _title, _line in heads:
        level_count[level] = level_count.get(level, 0) + 1

    # The first section that actually prints a field is the first record. On a
    # listing page the page title is a heading too, and its section is only the
    # intro (`# Top AI Companies` followed by prose, then `## Mistral`), so
    # starting at the first heading unconditionally made the rung read the intro
    # and return nothing at all. Probing each section for a labelled field skips
    # page furniture and finds the first real record.
    #
    # A repeated heading level is tried first: that is what makes a listing, and
    # it stops an intro line such as `Updated: 2024` from selecting the page-title
    # section and then collecting the first match for each field across *several*
    # records. A single-record page falls back to the first label-bearing section.
    def _first_record_heading(require_repeated: bool):
        for pos, (start, level, title, raw_line) in enumerate(heads):
            if require_repeated and level_count.get(level, 0) < 2:
                continue
            probe_end = heads[pos + 1][0] if pos + 1 < len(heads) else len(lines)
            if _opens_a_record(lines[start + 1:probe_end]):
                return pos, start, level, title, raw_line
        return None

    chosen = _first_record_heading(True) or _first_record_heading(False)
    if chosen is None:
        return {}

    _pos, start, level, title, raw_line = chosen
    # Everything under the record heading, including its sub-headings, up to the
    # next heading at the same or a higher level.
    section_end = len(lines)
    for i, other_level, _t, _l in heads:
        if i > start and other_level <= level:
            section_end = i
            break

    out: dict[str, tuple[str, str]] = {}
    seen: set[str] = set()
    for line in lines[start + 1:section_end]:
        m = _label_match(line)
        if not m:
            continue
        label, value = m.group(1).strip(), _clean_value(m.group(2))
        if not _usable(label, value):
            continue
        for spec in by_spec:
            name = str(spec.get("name") or "")
            if not name or name in seen:
                continue
            if _label_matches(label, spec):
                out[name] = (value, line)
                seen.add(name)
                break

    # A heading is only a name on a *listing* page, where the same heading level
    # introduces more than one entity. On a single-record page it is the page's
    # own subject line — a job posting's is the role title, so inferring
    # `company_name` from it wrote "Senior Machine Learning Engineer" where Roku
    # belonged. The earlier guard counted headings but broke before the count
    # could ever reach two, so the name was never read at all; the level count
    # states the actual condition.
    #
    # Assigned after the labelled lines, so an explicit `Company: Roku` always
    # outranks an inferred heading. Structured routes run before this rung, so a
    # JSON-LD identity is likewise already in `seen` and cannot be overwritten.
    if title and len(title) <= 90 and level_count.get(level, 0) >= 2:
        for spec in by_spec:
            name = str(spec.get("name") or "")
            if name in seen:
                continue
            if name in _NAMEISH or name.endswith("_name"):
                out[name] = (title, raw_line)
                seen.add(name)

    return out


def rung(fields: list[dict], ctx: dict) -> dict[str, dict]:
    """Rung — read labelled lines out of the page's own stored markdown.

    Cheap enough to always run and safe enough to run before the model: it only
    fills a field when the page prints a label that names it, and it hands back
    the line as the quote so the gate can confirm the line came from the stored
    page. Structured routes (JSON, selectors, JSON-LD) run first, so a generic
    label match can never displace the page's own explicit declaration.
    """
    page = ctx.get("page") or {}
    markdown = page.get("markdown") or ""
    if not markdown:
        return {}

    out: dict[str, dict] = {}
    for name, (value, quote) in labelled_pairs(markdown, fields).items():
        out[name] = {"value": value, "quote": quote, "read_method": "labelled"}
    return out


METHODS = (*conf_svc.METHODS, "labelled")
