"""Test suite for pre-LLM deterministic intelligence layer."""
from app.intelligence.claim_deduper import deduplicate_propositions
from app.intelligence.entities import entity_registry
from app.intelligence.query_templates import generate_deterministic_queries
from app.intelligence.segmenter import segment_propositions
from app.intelligence.taxonomy import classify_taxonomy


def test_segmenter_identifies_factual_propositions():
    text = "Acme released version 4.2 on October 20, 2026. The Pro plan costs $99 per month with 50,000 requests per minute."
    props = segment_propositions(text)
    assert len(props) == 2
    assert props[0].versions != []
    assert props[0].dates != []
    assert props[1].currencies != []
    assert props[1].rate_units != []
    assert props[1].has_numerical_fact is True


def test_entity_registry_resolves_aliases():
    assert entity_registry.resolve("Open AI") == "OpenAI"
    assert entity_registry.resolve("Acme Corp") == "Acme"
    assert "openai.com" in entity_registry.official_domains("OpenAI")
    assert "acme.com" in entity_registry.official_domains("Acme Corp")


def test_taxonomy_classification():
    p1 = classify_taxonomy("Acme Pro costs $99/mo", "price")
    assert p1.category == "pricing"
    assert p1.default_volatility == "fast"

    p2 = classify_taxonomy("Groq supports 8,000 TPM limit", "rate_limit")
    assert p2.category == "api_limit"


def test_scope_aware_deduplication():
    c1 = {"subject": "Acme", "predicate": "price", "object_raw": "$99", "claim_text": "Acme Pro costs $99 per month"}
    c2 = {"subject": "Acme", "predicate": "price", "object_raw": "$99", "claim_text": "The monthly fee for Acme Pro is $99"}
    # Different scope: per user
    c3 = {"subject": "Acme", "predicate": "price", "object_raw": "$99", "claim_text": "Acme Pro costs $99 per user per month"}

    deduped = deduplicate_propositions([c1, c2, c3])
    # c1 and c2 merge, c3 remains distinct!
    assert len(deduped) == 2
    assert len(deduped[0]["merged_spans"]) == 2


def test_deterministic_query_templates():
    queries = generate_deterministic_queries("Acme", "plan_price", "$129/month", "Acme Pro costs $129/month")
    assert len(queries) >= 1
    assert any("pricing" in q.lower() for q in queries)
