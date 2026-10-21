"""Turn a fetched JSON item or PDF into the text the corpus will store.

Two source shapes exist:

* India Code DSpace items are JSON. The provision is in one metadata field
  (`text_field`), as HTML fragments. The rest of the item is metadata.
* Judgments, the Constitution and the DSLSA schedule are PDFs. JUDIS-era files
  can carry a short binary preamble before `%PDF-`, so the header is located
  rather than assumed to start at byte zero.

`plain_html` is deliberately simple and deterministic. It does not try to be a
renderer; it removes tags, keeps line breaks where the source marked them, and
unescapes entities. For a statute, that is the whole content and every quote is
taken from its output.
"""
from __future__ import annotations

import html
import io
import json
import re


def first(metadata: dict, key: str) -> str:
    values = metadata.get(key) or []
    if not values:
        return ""
    return str(values[0].get("value") or "")


def plain_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.I)
    value = re.sub(r"</p\s*>", "\n", value, flags=re.I)
    value = re.sub(r"<hr[^>]*>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    return value.strip()


def pdf_text(content: bytes) -> str:
    from pypdf import PdfReader

    start = content.find(b"%PDF-")
    if start < 0:
        raise ValueError("body is not a PDF (no %PDF header)")
    reader = PdfReader(io.BytesIO(content[start:]))
    pages = [(page.extract_text() or "") for page in reader.pages]
    return "\n\n".join(pages).strip()


def dspace_item(content: bytes) -> dict:
    payload = json.loads(content.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("DSpace item is not a JSON object")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("DSpace item has no metadata object")
    return metadata


def constitution_article(body: str, section: str, title: str) -> str:
    """Slice the named article out of the full Constitution PDF.

    Storing the whole 400-page Constitution under both article ids duplicated
    the text and, worse, made the first paragraph of the Preamble the "summary"
    of Article 21. The corpus row is an article, so the stored text is too.

    The start is the article heading (with its optional amendment bracket); the
    end is the next article heading. If either cannot be found, the caller keeps
    the full text rather than silently storing a wrong excerpt.
    """
    number = (section or "").replace("Art.", "").strip()
    if not number or not body:
        return body
    heading = (title or "").split(" - ", 1)[-1].strip()
    starts = [f"{number}. {heading}", f"2[{number}. {heading}",
              f"{number}. "]
    start = -1
    for candidate in starts:
        start = body.rfind(candidate)
        if start >= 0:
            break
    if start < 0:
        return body
    rest = body[start + 1:]
    match = re.search(r"\n\s*(?:2\[)?\d{1,3}[A-Z]?\.\s", rest)
    end = start + 1 + match.start() if match else len(body)
    excerpt = body[start:end].strip()
    return excerpt if len(excerpt) >= 40 else body


def body_text(document: dict, content: bytes, content_type: str = "") -> str:
    """The source's own text, before the corpus metadata header is added."""
    kind = (content_type or document.get("content_type") or "").lower()
    if "json" in kind or content.lstrip()[:1] in (b"{", b"["):
        metadata = dspace_item(content)
        field = str(document.get("text_field") or "")
        text = plain_html(first(metadata, field)) if field else ""
        if len(text) < 40:
            raise ValueError(f"metadata field {field!r} is empty or too short")
        return text
    if content.find(b"%PDF-") >= 0:
        text = pdf_text(content)
        if len(text) < 40:
            raise ValueError("PDF has no extractable text (OCR required)")
        return text
    text = content.decode("utf-8", errors="replace").strip()
    if len(text) < 40:
        raise ValueError("body has no extractable text")
    return text
