"""Pre-LLM deterministic intelligence layer.

Performs proposition segmentation, entity alias normalization, fixed taxonomy
classification, scope-aware claim deduplication, and deterministic query planning.
"""
from app.intelligence.claim_deduper import deduplicate_propositions
from app.intelligence.entities import entity_registry
from app.intelligence.query_templates import generate_deterministic_queries
from app.intelligence.segmenter import segment_propositions
from app.intelligence.taxonomy import classify_taxonomy

__all__ = [
    "segment_propositions",
    "entity_registry",
    "classify_taxonomy",
    "deduplicate_propositions",
    "generate_deterministic_queries",
]
