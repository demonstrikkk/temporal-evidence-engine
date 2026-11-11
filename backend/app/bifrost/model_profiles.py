"""Model capability registry and task-aware routing for Bifrost inference.

Maintains capabilities of configured models and routes verification & synthesis
stages to appropriate models without exceeding context or quota limits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings


@dataclass(frozen=True)
class ModelCapability:
    model_id: str
    supports_json_mode: bool = True
    supports_streaming: bool = True
    max_context: int = 8192
    task_profiles: list[str] = field(default_factory=list)
    quota_profile: str = "free_account"
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0


# Default known capabilities
_KNOWN_CAPABILITIES: dict[str, ModelCapability] = {
    "groq/openai/gpt-oss-20b": ModelCapability(
        model_id="groq/openai/gpt-oss-20b",
        supports_json_mode=True,
        supports_streaming=True,
        max_context=8192,
        task_profiles=["extract_claims", "propose_relationship", "replacement_value", "plan_research", "synthesize_answer", "repair_answer"],
        quota_profile="groq_free",
    ),
    "groq/llama-3.3-70b-versatile": ModelCapability(
        model_id="groq/llama-3.3-70b-versatile",
        supports_json_mode=True,
        supports_streaming=True,
        max_context=128000,
        task_profiles=["propose_relationship", "synthesize_answer", "repair_answer"],
        quota_profile="groq_free",
    ),
    "groq/llama-3.1-8b-instant": ModelCapability(
        model_id="groq/llama-3.1-8b-instant",
        supports_json_mode=True,
        supports_streaming=True,
        max_context=128000,
        task_profiles=["extract_claims", "plan_research"],
        quota_profile="groq_free",
    ),
    "ollama/llama3.2:1b": ModelCapability(
        model_id="ollama/llama3.2:1b",
        supports_json_mode=False,
        supports_streaming=True,
        max_context=4096,
        task_profiles=["plan_research"],
        quota_profile="local_free",
    ),
}


class ModelRegistry:
    """Registry of models and their capabilities with task-specific routing."""

    def __init__(self) -> None:
        self._capabilities: dict[str, ModelCapability] = dict(_KNOWN_CAPABILITIES)

    def register(self, cap: ModelCapability) -> None:
        self._capabilities[cap.model_id] = cap

    def get_capability(self, model_id: str) -> ModelCapability:
        if model_id in self._capabilities:
            return self._capabilities[model_id]
        # Safe default capability profile
        return ModelCapability(
            model_id=model_id,
            supports_json_mode=True,
            supports_streaming=True,
            max_context=8192,
            task_profiles=["general"],
            quota_profile="unknown",
        )

    def model_for_stage(self, stage: str) -> str:
        """Task-aware routing: assigns the configured model profile for the requested stage."""
        stage_map = {
            "extract_claims": getattr(settings, "BIFROST_MODEL_EXTRACTOR", "groq/openai/gpt-oss-20b"),
            "repair_coverage": getattr(settings, "BIFROST_MODEL_EXTRACTOR", "groq/openai/gpt-oss-20b"),
            "plan_research": getattr(settings, "BIFROST_MODEL_EXTRACTOR", "groq/openai/gpt-oss-20b"),
            "propose_relationship": getattr(settings, "BIFROST_MODEL_JUDGE", "groq/openai/gpt-oss-20b"),
            "replacement_value": getattr(settings, "BIFROST_MODEL_JUDGE", "groq/openai/gpt-oss-20b"),
            "synthesize_answer": getattr(settings, "BIFROST_MODEL_SYNTHESIZER", None) or getattr(settings, "BIFROST_MODEL_JUDGE", "groq/openai/gpt-oss-20b"),
            "repair_answer": getattr(settings, "BIFROST_MODEL_SYNTHESIZER", None) or getattr(settings, "BIFROST_MODEL_JUDGE", "groq/openai/gpt-oss-20b"),
        }
        chosen = stage_map.get(stage)
        if not chosen:
            chosen = getattr(settings, "BIFROST_MODEL_EXTRACTOR", "groq/openai/gpt-oss-20b")
        return str(chosen)

    def describe_all(self) -> list[dict[str, Any]]:
        return [
            {
                "model_id": c.model_id,
                "supports_json_mode": c.supports_json_mode,
                "max_context": c.max_context,
                "task_profiles": c.task_profiles,
                "quota_profile": c.quota_profile,
            }
            for c in self._capabilities.values()
        ]


# Global singleton instance
model_registry = ModelRegistry()
