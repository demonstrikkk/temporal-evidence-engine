"""Extraction post-processing: the model proposes, the answer constrains."""
from __future__ import annotations

import pytest

from app.verify.extract import postprocess

ANSWER = ("Acme Pro costs $99 per month. Acme is headquartered in Berlin and "
          "was founded in 2015. I think Acme is the best vendor in the market.")


def _raw(**overrides):
    base = {"claim_text": "Acme Pro costs $99 per month",
            "subject": "Acme Pro", "predicate": "price",
            "object_raw": "$99/month", "claim_type": "time_sensitive",
            "time_sensitive": True, "volatility_class": "high",
            "extraction_confidence": 0.8}
    base.update(overrides)
    return base


def test_a_located_claim_keeps_absolute_offsets():
    claims = postprocess({"claims": [_raw()]}, ANSWER)
    assert len(claims) == 1
    claim = claims[0]
    assert ANSWER[claim.span_start:claim.span_end] == claim.claim_text
    assert claim.object_normalized["type"] == "money"


def test_a_claim_that_is_not_in_the_answer_is_dropped():
    claims = postprocess({"claims": [_raw(claim_text="Acme Pro actually costs $79 per month")]},
                         ANSWER)
    assert claims == []


def test_opinions_and_predictions_are_not_factual_claims():
    claims = postprocess({"claims": [
        _raw(claim_text="I think Acme is the best vendor", claim_type="opinion",
             object_raw="best"),
        _raw(claim_text="Acme will launch in 2015", claim_type="prediction",
             object_raw="2015"),
    ]}, ANSWER)
    assert claims == []


def test_duplicate_claims_are_collapsed():
    claims = postprocess({"claims": [
        _raw(), _raw(claim_text="Acme Pro costs $99 per month."),
    ]}, ANSWER)
    assert len(claims) == 1


def test_a_missing_value_is_derived_from_the_claim_text():
    claims = postprocess({"claims": [_raw(object_raw="")]}, ANSWER)
    assert claims[0].object_raw == "$99 per month"
    assert claims[0].object_normalized["type"] == "money"


def test_a_relation_without_a_value_is_checked_against_its_own_words():
    claims = postprocess({"claims": [
        _raw(claim_text="Acme is headquartered in Berlin", object_raw="",
             subject="Acme", predicate="headquarters")]}, ANSWER)
    assert claims[0].object_raw == "Acme is headquartered in Berlin"


def test_a_non_json_shape_is_an_error_not_an_empty_answer():
    with pytest.raises(ValueError):
        postprocess({"items": []}, ANSWER)


def test_an_unknown_claim_type_does_not_invent_a_new_enum():
    claims = postprocess({"claims": [_raw(claim_type="speculative")]}, ANSWER)
    assert claims[0].claim_type.value == "factual"


def test_both_extraction_prompts_name_the_stale_fact_categories_by_name():
    """The brief names pricing, API versions, laws and leadership.

    Recall on exactly those categories is the product's headline requirement, and
    it is bought with prompt words, not with code. A future trim that drops them
    would be invisible in every other test: the extractor would still work, it
    would just stop looking. Both passes are checked because the repair pass is
    what catches a volatile sentence the first pass missed.
    """
    from app.verify import extract

    for system in (extract._SYSTEM, extract._REPAIR_SYSTEM):
        lowered = system.casefold()
        for word in ("price", "version", "law", "leader"):
            assert word in lowered, f"{word!r} is no longer named in the prompt"
