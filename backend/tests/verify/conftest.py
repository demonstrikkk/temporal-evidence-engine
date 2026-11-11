"""Fixtures for the verifier: a gateway and a search engine made of dicts.

These are not mocks of internal functions — they implement the same two external
interfaces the production code uses (`complete`, `search`/`extract`), so a test
exercises the real orchestrator, the real settlement gate and the real decision
table. Nothing here stubs `decide`, `settle` or storage.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.bifrost.client import BifrostResult
from app.research.tavily import ExtractOutcome, FailedUrl, SearchHit, SearchOutcome
from app.verify.store import LocalStore, now
from app.verify.value_normalizer import normalize_value


@dataclass
class FakeGateway:
    """`responses` maps a URL (found in the prompt) to a proposal payload."""

    claims: list[dict[str, Any]] = field(default_factory=list)
    #: Proposals for the one coverage-repair call. Empty by default, which is
    #: also the honest answer: a run whose first pass covered every assertion
    #: makes no repair call at all.
    repair_claims: list[dict[str, Any]] = field(default_factory=list)
    responses: dict[str, dict[str, Any]] = field(default_factory=dict)
    fail_stage: str = ""
    calls: list[str] = field(default_factory=list)
    base_url: str = "http://fake-gateway"
    #: `live` — a call was made. `snapshot` — this answer came from the cache and
    #: the run must say so instead of looking like a fresh spend.
    status: str = "live"

    def complete(self, prompt: str, *, stage: str, system: str = "",
                 model: str | None = None, claim_id: str | None = None,
                 temperature: float = 0.0, max_tokens: int = 1400,
                 json_mode: bool = True) -> BifrostResult:
        self.calls.append(stage)
        if self.fail_stage and self.fail_stage == stage:
            from app.bifrost.client import BifrostError
            raise BifrostError("fatal", "the fake gateway was told to fail here")
        if stage == "extract_claims":
            payload = {"claims": self.claims}
        elif stage == "repair_coverage":
            payload = {"claims": self.repair_claims}
        else:
            payload = {"relationship": "silent", "quote": "",
                       "candidate_value": "", "reasoning": "no rule matched"}
            for url, response in self.responses.items():
                if url in prompt:
                    payload = response
                    break
        return BifrostResult(
            text=json.dumps(payload), provider="fake", model=model or "fake/model",
            request_id=f"req-{len(self.calls)}", input_tokens=10, output_tokens=5,
            cost_usd=0.0, latency_ms=1, fallback_attempts=0, status=self.status,
            created_at=now(), raw=payload)


@dataclass
class FakeTavily:
    hits: dict[str, list[SearchHit]] = field(default_factory=dict)
    pages: dict[str, str] = field(default_factory=dict)
    fail_search: bool = False
    searches: list[str] = field(default_factory=list)
    #: URLs handed to `extract`, in call order — plus which URLs each call
    #: covered. Used to prove a shared page is extracted once per run, not once
    #: per claim that shares it.
    extracts: list[dict[str, Any]] = field(default_factory=list)

    def search(self, query: str, **_: Any) -> SearchOutcome:
        self.searches.append(query)
        if self.fail_search:
            from app.research.tavily import ResearchError
            raise ResearchError("transient", "the fake search is down")
        results = self.hits.get(query) or [hit for group in self.hits.values()
                                           for hit in group]
        # The planner may derive several queries; each search may return the
        # same documents, which is exactly the dedupe path under test.
        return SearchOutcome(query=query, hits=list(results), failed=[],
                             request_id=f"search-{len(self.searches)}",
                             credits=1, status="live", raw={})

    def extract(self, urls: list[str], **_: Any) -> ExtractOutcome:
        self.extracts.append({"urls": list(urls)})
        pages = {url: self.pages[url] for url in urls if url in self.pages}
        failed = [FailedUrl(url, "not found") for url in urls if url not in self.pages]
        return ExtractOutcome(pages=pages, failed=failed, request_id="extract-1",
                              credits=1, status="live", raw={})


@pytest.fixture
def make_gateway():
    def _make(*, claims=None, repair_claims=None, responses=None,
              fail_stage="", status="live"):
        return FakeGateway(claims=list(claims or []),
                           repair_claims=list(repair_claims or []),
                           responses=dict(responses or {}),
                           fail_stage=fail_stage, status=status)
    return _make


@pytest.fixture
def make_tavily():
    def _make(*, pages=None, fail_search=False):
        hits = {url: [SearchHit(title=url.rsplit("/", 1)[-1], url=url,
                                snippet="", score=0.9, published_date=None)]
                for url in (pages or {})}
        return FakeTavily(hits=hits, pages=dict(pages or {}),
                          fail_search=fail_search)
    return _make


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "state"))


@pytest.fixture
def claim_payload():
    def _claim(**overrides):
        base = {
            "claim_text": "Acme Pro costs $99 per month",
            "subject": "Acme Pro",
            "predicate": "price",
            "object_raw": "$99/month",
            "claim_type": "time_sensitive",
            "time_sensitive": True,
            "volatility_class": "high",
            "extraction_confidence": 0.9,
        }
        base.update(overrides)
        base["object_normalized"] = normalize_value(base["object_raw"])
        return base
    return _claim
