"""Sources are canonical, append-only, and excerpted with absolute offsets."""
from __future__ import annotations

from datetime import date

from app.research.sources import (canonicalize_url, content_hash, domain_of,
                                  excerpt_windows, render_windows,
                                  save_revision, save_source)


def test_tracking_parameters_and_fragments_are_removed():
    assert canonicalize_url(
        "https://www.acme.test/pricing/?utm_source=newsletter&b=2&a=1#plans"
    ) == "https://www.acme.test/pricing?a=1&b=2"


def test_http_and_default_ports_become_one_url():
    assert canonicalize_url("http://acme.test:80/pricing") == \
        "https://acme.test/pricing"


def test_domain_drops_www():
    assert domain_of("https://www.acme.test/x") == "acme.test"


def test_the_same_bytes_reuse_a_revision_and_new_bytes_append(store):
    source = save_source(store, "https://acme.test/pricing", "Pricing")
    first = save_revision(store, source=source, content="Acme Pro costs $99.",
                          published_date=date(2026, 9, 1),
                          published_date_source="tavily_search_metadata")
    again = save_revision(store, source=source, content="Acme Pro costs $99.")
    changed = save_revision(store, source=source, content="Acme Pro costs $129.")

    assert first.revision_id == again.revision_id
    assert again.reused is True
    assert changed.revision_id != first.revision_id
    assert changed.content_hash == content_hash("Acme Pro costs $129.")
    assert store.get_revision(first.revision_id)["content"] == "Acme Pro costs $99."
    assert store.get_revision(changed.revision_id)["content"] == "Acme Pro costs $129."


def test_excerpt_offsets_address_the_full_stored_text():
    content = ("filler " * 600 + " Acme Pro costs $99 per month. "
               + "unrelated " * 600)
    windows = excerpt_windows(content, "Acme Pro price", max_chars=1200)
    assert windows, "a term hit should produce a window"
    for window in windows:
        assert content[window["start"]:window["end"]] == window["text"]
    assert any("Acme Pro" in window["text"] for window in windows)


def test_a_short_document_is_not_chunked():
    windows = excerpt_windows("Acme Pro costs $99.", "Acme", max_chars=1000)
    assert len(windows) == 1
    assert windows[0]["start"] == 0


def test_the_model_input_states_its_offsets():
    rendered = render_windows("Acme Pro costs $99.", "Acme")
    assert "[window 1 | chars 0-19]" in rendered
    assert "Acme Pro costs $99." in rendered
