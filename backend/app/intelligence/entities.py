"""Entity registry and alias normalization.

Provides canonical entity mapping and official domain discovery so research
planners search official sources first and avoid searching the wrong entity.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EntityInfo:
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    official_domains: list[str] = field(default_factory=list)
    entity_type: str = "organization"  # organization, product, person, standard


_SEED_ENTITIES: list[EntityInfo] = [
    EntityInfo(
        canonical_name="OpenAI",
        aliases=["open ai", "openai, inc.", "openai inc", "openai llc"],
        official_domains=["openai.com"],
        entity_type="organization",
    ),
    EntityInfo(
        canonical_name="Anthropic",
        aliases=["anthropic pbc", "anthropic ai"],
        official_domains=["anthropic.com"],
        entity_type="organization",
    ),
    EntityInfo(
        canonical_name="Groq",
        aliases=["groq inc", "groq ai", "groqcloud"],
        official_domains=["groq.com", "console.groq.com"],
        entity_type="organization",
    ),
    EntityInfo(
        canonical_name="Google",
        aliases=["google llc", "google cloud", "alphabet"],
        official_domains=["google.com", "cloud.google.com"],
        entity_type="organization",
    ),
    EntityInfo(
        canonical_name="Stripe",
        aliases=["stripe inc", "stripe payments"],
        official_domains=["stripe.com"],
        entity_type="organization",
    ),
    EntityInfo(
        canonical_name="Tavily",
        aliases=["tavily ai", "tavily search"],
        official_domains=["tavily.com"],
        entity_type="organization",
    ),
    EntityInfo(
        canonical_name="Acme",
        aliases=["acme corp", "acme inc", "acme corporation"],
        official_domains=["acme.com"],
        entity_type="organization",
    ),
]


class EntityRegistry:
    """In-memory entity registry with alias resolution and domain lookup."""

    def __init__(self) -> None:
        self._entities: dict[str, EntityInfo] = {}
        self._alias_map: dict[str, str] = {}

        for ent in _SEED_ENTITIES:
            self.register(ent)

    def register(self, entity: EntityInfo) -> None:
        c_key = entity.canonical_name.lower().strip()
        self._entities[c_key] = entity
        self._alias_map[c_key] = entity.canonical_name
        for alias in entity.aliases:
            self._alias_map[alias.lower().strip()] = entity.canonical_name

    def resolve(self, raw_name: str) -> str:
        """Resolve an alias to its canonical name if known."""
        clean = raw_name.lower().strip()
        # Remove punctuation like Inc., Corp.
        normalized = re.sub(r"[,\.]", "", clean).strip()
        if normalized in self._alias_map:
            return self._alias_map[normalized]
        if clean in self._alias_map:
            return self._alias_map[clean]
        return raw_name.strip()

    def official_domains(self, name_or_alias: str) -> list[str]:
        """Return official domains for the entity if registered."""
        canonical = self.resolve(name_or_alias).lower()
        if canonical in self._entities:
            return list(self._entities[canonical].official_domains)
        return []


# Global singleton
entity_registry = EntityRegistry()
