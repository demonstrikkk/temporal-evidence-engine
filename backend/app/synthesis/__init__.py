"""Answer synthesis, context compilation, citation validation, and fallback rendering."""
from app.synthesis.answer_generator import generate_grounded_answer
from app.synthesis.citation_validator import validate_citations
from app.synthesis.context_compiler import compile_evidence_context
from app.synthesis.fallback_renderer import render_deterministic_fallback

__all__ = [
    "compile_evidence_context",
    "generate_grounded_answer",
    "validate_citations",
    "render_deterministic_fallback",
]
