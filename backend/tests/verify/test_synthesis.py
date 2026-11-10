"""Test suite for post-LLM answer synthesis and citation validation."""
from app.synthesis.citation_validator import validate_citations
from app.synthesis.fallback_renderer import render_deterministic_fallback


def test_citation_validator_accepts_valid_citations():
    context = {
        "verified_claims": [
            {"claim_id": "c1", "text": "Acme Pro costs $129/month", "value_raw": "$129/month", "evidence_ids": ["e_001"]}
        ],
        "sources": [
            {"evidence_id": "e_001", "quote": "Pro plan is $129/month", "source_title": "Acme", "url": "https://acme.com"}
        ]
    }
    answer = "Acme Pro currently costs $129/month [e_001]."
    val = validate_citations(answer, context)
    assert val.is_valid is True
    assert val.grounding_score == 1.0
    assert "e_001" in val.citations_used


def test_citation_validator_rejects_hallucinated_citations():
    context = {
        "verified_claims": [
            {"claim_id": "c1", "text": "Acme Pro costs $129/month", "value_raw": "$129/month", "evidence_ids": ["e_001"]}
        ],
        "sources": [
            {"evidence_id": "e_001", "quote": "Pro plan is $129/month", "source_title": "Acme", "url": "https://acme.com"}
        ]
    }
    answer = "Acme Pro costs $129/month [e_999]."
    val = validate_citations(answer, context)
    assert val.is_valid is False
    assert any("e_999" in err for err in val.errors)


def test_deterministic_fallback_renderer():
    context = {
        "user_question": "What does Acme Pro cost?",
        "verified_claims": [
            {"claim_id": "c1", "text": "Acme Pro costs $129/month", "status": "supported", "evidence_ids": ["e_001"]}
        ],
        "unverifiable_claims": [
            {"claim_id": "c2", "text": "Acme has 500 engineers", "reason_code": "no_source"}
        ],
        "sources": [
            {"evidence_id": "e_001", "quote": "Pro plan is $129/month", "source_title": "Acme Pricing", "url": "https://acme.com/pricing", "event_date": "2026-11-01"}
        ]
    }
    rendered = render_deterministic_fallback(context)
    assert "Acme Pro costs $129/month [e_001]" in rendered
    assert "Acme has 500 engineers" in rendered
    assert "https://acme.com/pricing" in rendered
