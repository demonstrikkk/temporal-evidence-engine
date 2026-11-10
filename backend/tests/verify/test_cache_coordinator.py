"""Test suite for multi-layer CacheCoordinator (L0-L5)."""
import time
from app.cache.coordinator import CacheCoordinator
from app.verify.store import LocalStore


def test_l0_single_flight(tmp_path):
    store = LocalStore(str(tmp_path))
    coord = CacheCoordinator(store)

    calls = 0

    def work():
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return "result-ok"

    res = coord.single_flight("test-flight-1", work)
    assert res == "result-ok"
    assert calls == 1


def test_l1_deterministic_computation_cache(tmp_path):
    store = LocalStore(str(tmp_path))
    coord = CacheCoordinator(store)

    assert coord.get_l1("parse_date", {"text": "2026-11-10"}) is None

    coord.put_l1("parse_date", {"text": "2026-11-10"}, "2026-11-10", ttl_seconds=3600)
    cached = coord.get_l1("parse_date", {"text": "2026-11-10"})
    assert cached == "2026-11-10"
    assert coord.stats()["l1_hits"] >= 1


def test_l2_exact_llm_kv_cache(tmp_path):
    store = LocalStore(str(tmp_path))
    coord = CacheCoordinator(store)

    hit = coord.get_l2("groq/model", "extract", "sys", "user")
    assert hit is None

    coord.put_l2("groq/model", "extract", "sys", "user", {"claims": ["c1"]})
    hit2 = coord.get_l2("groq/model", "extract", "sys", "user")
    assert hit2 == {"claims": ["c1"]}
    assert coord.stats()["saved_model_calls"] >= 1


def test_l3_tavily_query_cache(tmp_path):
    store = LocalStore(str(tmp_path))
    coord = CacheCoordinator(store)

    hit = coord.get_l3("acme pro pricing")
    assert hit is None

    coord.put_l3("acme pro pricing", {"results": [{"url": "https://acme.com"}]})
    hit2 = coord.get_l3("acme pro pricing")
    assert hit2 == {"results": [{"url": "https://acme.com"}]}
    assert coord.stats()["saved_tavily_searches"] >= 1


def test_l5_claim_evidence_reuse(tmp_path):
    store = LocalStore(str(tmp_path))
    coord = CacheCoordinator(store)

    evidence = [{"quote": "$129/mo", "url": "https://acme.com/pricing"}]
    coord.put_l5("Acme", "pricing", evidence, claim_type="pricing")

    reused = coord.get_l5("Acme", "pricing")
    assert reused == evidence
    assert coord.stats()["l5_hits"] >= 1
