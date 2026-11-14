"""One page is one failure row.

Tavily reports an extraction failure *and* returns no text for the same URL often
enough that the two used to be recorded separately. The rail then showed a single
URL twice with two different reasons, and `failed_sources` counted one page as
two failures — a run claiming more failed sources than pages it asked for.

The extract loop is where that was fixed, so these tests drive the real
orchestrator through the two interfaces it uses (`search`, `extract`) and read the
rail it actually wrote. Nothing here stubs `emit`, storage or `decide`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.research.tavily import (ExtractOutcome, FailedUrl, SearchHit,
                                 SearchOutcome)
from app.verify.orchestrator import Verifier, create_run
from app.verify.schemas import AnswerIn

GEN = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
PAGE_URL = "https://acme.test/pricing"
DEAD_URL = "https://acme.test/gone"
PRICING_PAGE = (
    "Acme pricing\n\n"
    "Acme Pro costs $99 per month, billed monthly. "
    "Enterprise is $499 per month."
)


class _Tavily:
    """A search engine whose extraction outcome is exactly what a test says.

    `failed` is returned as Tavily's own `failed_results`, independently of
    whether `pages` also holds text for that URL — which is the overlap the
    accounting has to survive.
    """

    def __init__(self, urls, pages=None, failed=None):
        self.urls = list(urls)
        self.pages = dict(pages or {})
        self.failed = list(failed or [])
        self.searches: list[str] = []
        self.extracts: list[dict] = []

    def search(self, query: str, **_) -> SearchOutcome:
        self.searches.append(query)
        hits = [SearchHit(title=url.rsplit("/", 1)[-1], url=url, snippet="",
                          score=0.9, published_date=None)
                for url in self.urls]
        return SearchOutcome(query=query, hits=hits, failed=[],
                             request_id=f"search-{len(self.searches)}",
                             credits=1, status="live", raw={})

    def extract(self, urls: list[str], **_) -> ExtractOutcome:
        self.extracts.append({"urls": list(urls)})
        pages = {url: self.pages[url] for url in urls if url in self.pages}
        failed = [f for f in self.failed if f.url in urls]
        return ExtractOutcome(pages=pages, failed=failed,
                              request_id="extract-1", credits=1,
                              status="live", raw={})


def _run(store, gateway, tavily):
    run = create_run(store, AnswerIn(
        answer="Acme Pro costs $99 per month",
        prompt="What does Acme Pro cost?",
        generated_at=GEN,
        generated_at_source="user_supplied",
    ))
    Verifier(store, run.run_id, bifrost=gateway, tavily=tavily).execute()
    return run


def _failures(store, run_id):
    return [e for e in store.list_events(run_id)
            if e["event_type"] == "source.failed"]


def _counters(store, run_id):
    return store.list_events(run_id)[-1]["payload"]["counters"]


def test_a_url_reported_failed_with_no_text_is_recorded_once(
        store, make_gateway, claim_payload):
    """Tavily's reason wins, and the empty read is not a second failure."""
    tavily = _Tavily([PAGE_URL, DEAD_URL],
                     pages={PAGE_URL: PRICING_PAGE},
                     failed=[FailedUrl(DEAD_URL, "Failed to fetch url")])

    run = _run(store, make_gateway(claims=[claim_payload()]), tavily)

    rows = [e["payload"] for e in _failures(store, run.run_id)
            if e["payload"]["url"] == DEAD_URL]
    assert len(rows) == 1, "one page, one failure row"
    assert rows[0]["error"] == "Failed to fetch url", (
        "the upstream cause is the reason; 'no text returned' is its symptom")
    assert _counters(store, run.run_id)["failed_sources"] == 1


def test_a_url_with_no_text_and_no_reported_reason_still_says_so(
        store, make_gateway, claim_payload):
    """Silence from Tavily is the only case where we supply the reason."""
    tavily = _Tavily([PAGE_URL, DEAD_URL], pages={PAGE_URL: PRICING_PAGE})

    run = _run(store, make_gateway(claims=[claim_payload()]), tavily)

    rows = [e["payload"] for e in _failures(store, run.run_id)
            if e["payload"]["url"] == DEAD_URL]
    assert len(rows) == 1
    assert rows[0]["error"] == "no text returned"
    assert _counters(store, run.run_id)["failed_sources"] == 1


def test_a_page_that_yielded_text_is_not_reported_failed(
        store, make_gateway, claim_payload):
    """Stored text and a failure row for the same URL would contradict."""
    tavily = _Tavily([PAGE_URL],
                     pages={PAGE_URL: PRICING_PAGE},
                     failed=[FailedUrl(PAGE_URL, "Failed to fetch url")])

    run = _run(store, make_gateway(claims=[claim_payload()]), tavily)

    assert _failures(store, run.run_id) == [], (
        "a page whose text resolved did not fail, whatever the metadata says")
    assert _counters(store, run.run_id)["failed_sources"] == 0
    assert _counters(store, run.run_id)["sources"] >= 1
