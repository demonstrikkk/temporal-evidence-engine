"""Compact structured conversation memory.

Stores active entities, topics, claim IDs, and depth preferences rather than
dumping long chat transcripts into model prompts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StructuredMemory:
    conversation_id: str
    active_entities: list[str] = field(default_factory=list)
    active_topics: list[str] = field(default_factory=list)
    active_claim_ids: list[str] = field(default_factory=list)
    last_run_id: str | None = None
    unresolved_questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "active_entities": self.active_entities,
            "active_topics": self.active_topics,
            "active_claim_ids": self.active_claim_ids,
            "last_run_id": self.last_run_id,
            "unresolved_questions": self.unresolved_questions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], conv_id: str = "") -> "StructuredMemory":
        return cls(
            conversation_id=str(data.get("conversation_id") or conv_id),
            active_entities=list(data.get("active_entities") or []),
            active_topics=list(data.get("active_topics") or []),
            active_claim_ids=list(data.get("active_claim_ids") or []),
            last_run_id=data.get("last_run_id"),
            unresolved_questions=list(data.get("unresolved_questions") or []),
        )

    def update(
        self,
        entities: list[str] | None = None,
        topics: list[str] | None = None,
        claim_ids: list[str] | None = None,
        run_id: str | None = None,
    ) -> None:
        if entities:
            for e in entities:
                if e and e not in self.active_entities:
                    self.active_entities.append(e)
            self.active_entities = self.active_entities[-10:]
        if topics:
            for t in topics:
                if t and t not in self.active_topics:
                    self.active_topics.append(t)
            self.active_topics = self.active_topics[-10:]
        if claim_ids:
            for cid in claim_ids:
                if cid and cid not in self.active_claim_ids:
                    self.active_claim_ids.append(cid)
            self.active_claim_ids = self.active_claim_ids[-20:]
        if run_id:
            self.last_run_id = run_id
