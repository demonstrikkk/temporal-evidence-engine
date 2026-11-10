"""Conversation manager orchestrating multi-turn research, evidence reuse, and synthesis."""
from __future__ import annotations

from typing import Any

from app.bifrost.client import BifrostClient
from app.conversation.context import classify_intent
from app.conversation.memory import StructuredMemory
from app.core.config import settings
from app.intelligence.entities import entity_registry
from app.synthesis.answer_generator import generate_grounded_answer
from app.synthesis.context_compiler import compile_evidence_context
from app.verify.orchestrator import Verifier, create_run
from app.verify.schemas import AnswerIn
from app.verify.store import VerifyStore, new_id, now


class ConversationManager:
    """Coordinates conversational research sessions and evidence grounding."""

    def __init__(self, store: VerifyStore) -> None:
        self.store = store

    def create_conversation(self, title: str = "", budget_mode: str = "balanced") -> dict[str, Any]:
        conv_id = new_id()
        row = {
            "id": conv_id,
            "title": title or "New Research Session",
            "budget_mode": budget_mode,
            "structured_memory": StructuredMemory(conversation_id=conv_id).to_dict(),
        }
        return self.store.create_conversation(row)

    def get_conversation(self, conv_id: str) -> dict[str, Any] | None:
        return self.store.get_conversation(conv_id)

    def list_conversations(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_conversations(limit)

    def list_messages(self, conv_id: str) -> list[dict[str, Any]]:
        return self.store.list_messages(conv_id)

    def process_turn(
        self,
        conv_id: str,
        user_text: str,
        *,
        bifrost: BifrostClient | None = None,
        tavily: Any = None,
    ) -> dict[str, Any]:
        """Process a user conversational research turn."""
        conv = self.store.get_conversation(conv_id)
        if not conv:
            raise ValueError(f"Conversation {conv_id} not found")

        # 1. Record user message
        user_msg = self.store.create_message({
            "conversation_id": conv_id,
            "role": "user",
            "content": user_text,
        })

        # 2. Memory & Intent
        raw_mem = conv.get("structured_memory") or {}
        memory = StructuredMemory.from_dict(raw_mem, conv_id)
        intent = classify_intent(user_text, memory)
        budget_mode = conv.get("budget_mode") or "balanced"

        client = bifrost or BifrostClient(self.store)

        run_id = memory.last_run_id

        # If new_topic or reverify or no previous run: run verification
        if intent in ("new_topic", "reverify") or not run_id:
            # If user asks to reverify, bypass research cache
            effective_cache_mode = "off" if intent == "reverify" else settings.CACHE_MODE
            answer_in = AnswerIn(
                answer=user_text,
                prompt=user_text,
                generated_at=now(),
                generated_at_source="user_supplied",
                budget_mode=budget_mode,
            )
            created = create_run(self.store, answer_in)
            run_id = created.run_id

            verifier = Verifier(self.store, run_id, bifrost=client, tavily=tavily)
            verifier.execute()

        # 3. Compile evidence context from the run
        context = compile_evidence_context(self.store, run_id, user_question=user_text)

        # 4. Synthesize grounded answer
        answer_text, validation = generate_grounded_answer(client, context)

        # 5. Extract entities for structured memory
        detected_entities = []
        for word in user_text.split():
            clean = word.strip("?,.:;\"'")
            if clean:
                resolved = entity_registry.resolve(clean)
                if resolved != clean and resolved not in detected_entities:
                    detected_entities.append(resolved)

        verified_claim_ids = [c["claim_id"] for c in context.get("verified_claims", [])]
        memory.update(
            entities=detected_entities,
            claim_ids=verified_claim_ids,
            run_id=run_id,
        )
        self.store.update_conversation(conv_id, structured_memory=memory.to_dict())

        # 6. Store and return assistant message
        assistant_msg = self.store.create_message({
            "conversation_id": conv_id,
            "role": "assistant",
            "content": answer_text,
            "run_id": run_id,
            "citations": context.get("sources", []),
            "quality_metrics": validation.quality_metrics,
        })

        return {
            "user_message": user_msg,
            "assistant_message": assistant_msg,
            "context": context,
            "validation": validation,
            "intent": intent,
        }
