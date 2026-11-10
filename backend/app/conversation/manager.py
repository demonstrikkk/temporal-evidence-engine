"""Conversation manager orchestrating multi-turn research, evidence reuse, and synthesis."""
from __future__ import annotations

from typing import Any

from app.bifrost.client import BifrostClient
from app.conversation.context import classify_intent
from app.conversation.memory import StructuredMemory
from app.core.config import settings
from app.intelligence.entities import entity_registry
from app.research.sources import find_existing_revisions
from app.synthesis.answer_generator import generate_grounded_answer
from app.synthesis.context_compiler import compile_evidence_context
from app.verify.decision import EvidenceFact, Relationship
from app.verify.orchestrator import Verifier, create_run
from app.verify.schemas import AnswerIn, ClaimStatus
from app.verify.store import VerifyStore, new_id, now, parse_iso


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

    # -- Claim-level follow-up research ------------------------------------

    def follow_up_claim(
        self,
        conv_id: str,
        claim_id: str,
        *,
        bifrost: BifrostClient | None = None,
        tavily: Any = None,
    ) -> dict[str, Any]:
        """Investigate an individual claim without repeating all prior work.

        Reuses previously collected evidence but performs a fresh live check
        (bypassing research cache) on the specific claim.
        """
        conv = self.store.get_conversation(conv_id)
        if not conv:
            raise ValueError(f"Conversation {conv_id} not found")

        claim = self.store.get_claim(claim_id)
        if not claim:
            raise ValueError(f"Claim {claim_id} not found")

        client = bifrost or BifrostClient(self.store)

        # Create a new verification run scoped to just this claim
        answer_in = AnswerIn(
            answer=claim["claim_text"],
            prompt=f"Follow-up research on claim: {claim['claim_text']}",
            generated_at=now(),
            generated_at_source="user_supplied",
            budget_mode="deep",  # Follow-up gets deeper research
            skip_non_factual=False,
        )
        created = create_run(self.store, answer_in)
        run_id = created.run_id

        verifier = Verifier(self.store, run_id, bifrost=client, tavily=tavily)
        verifier.execute()

        # Compile evidence context and synthesize
        context = compile_evidence_context(self.store, run_id, user_question=claim["claim_text"])
        answer_text, validation = generate_grounded_answer(client, context)

        # Store assistant message
        assistant_msg = self.store.create_message({
            "conversation_id": conv_id,
            "role": "assistant",
            "content": answer_text,
            "run_id": run_id,
            "citations": context.get("sources", []),
            "quality_metrics": validation.quality_metrics,
        })

        return {
            "claim_id": claim_id,
            "run_id": run_id,
            "assistant_message": assistant_msg,
            "context": context,
            "validation": validation,
        }

    # -- Historical comparison ---------------------------------------------

    def historical_comparison(
        self,
        run_id_a: str,
        run_id_b: str,
    ) -> dict[str, Any]:
        """Compare what sources said at two points in time.

        Distinguishes a genuinely changed fact from a newly published report.
        """
        run_a = self.store.get_run(run_id_a)
        run_b = self.store.get_run(run_id_b)
        if not run_a or not run_b:
            raise ValueError("One or both runs not found")

        claims_a = self.store.list_claims(run_id_a)
        claims_b = self.store.list_claims(run_id_b)

        # Match claims by claim_text
        claims_a_map = {c["claim_text"]: c for c in claims_a}
        claims_b_map = {c["claim_text"]: c for c in claims_b}

        comparisons: list[dict[str, Any]] = []
        for claim_text, claim_a in claims_a_map.items():
            claim_b = claims_b_map.get(claim_text)
            if not claim_b:
                continue

            evidence_a = self.store.list_evidence(str(claim_a["id"]))
            evidence_b = self.store.list_evidence(str(claim_b["id"]))

            comparisons.append({
                "claim_text": claim_text,
                "run_a": {
                    "run_id": run_id_a,
                    "status": claim_a.get("status"),
                    "reason_code": claim_a.get("reason_code"),
                    "evidence_count": len(evidence_a),
                    "evidence": evidence_a,
                },
                "run_b": {
                    "run_id": run_id_b,
                    "status": claim_b.get("status"),
                    "reason_code": claim_b.get("reason_code"),
                    "evidence_count": len(evidence_b),
                    "evidence": evidence_b,
                },
                "status_changed": claim_a.get("status") != claim_b.get("status"),
            })

        return {
            "run_a": run_id_a,
            "run_b": run_id_b,
            "comparisons": comparisons,
            "changed_count": sum(1 for c in comparisons if c["status_changed"]),
        }

    # -- Answer correction -------------------------------------------------

    def correct_answer(
        self,
        conv_id: str,
        message_id: str,
        corrected_text: str,
        *,
        bifrost: BifrostClient | None = None,
    ) -> dict[str, Any]:
        """Produce a revised answer using settled evidence.

        The original answer remains immutable; the correction is a new message
        with its own audit trail.
        """
        conv = self.store.get_conversation(conv_id)
        if not conv:
            raise ValueError(f"Conversation {conv_id} not found")

        client = bifrost or BifrostClient(self.store)

        # Re-synthesize from the existing run's evidence using corrected text
        memory = StructuredMemory.from_dict(
            conv.get("structured_memory") or {}, conv_id)
        run_id = memory.last_run_id
        if not run_id:
            raise ValueError("No previous run to correct")

        context = compile_evidence_context(self.store, run_id, user_question=corrected_text)
        answer_text, validation = generate_grounded_answer(client, context)

        assistant_msg = self.store.create_message({
            "conversation_id": conv_id,
            "role": "assistant",
            "content": answer_text,
            "run_id": run_id,
            "citations": context.get("sources", []),
            "quality_metrics": validation.quality_metrics,
        })

        return {
            "corrected_message": assistant_msg,
            "context": context,
            "validation": validation,
        }

    # -- Challenge-and-review workflow -------------------------------------

    def challenge_review(
        self,
        conv_id: str,
        claim_id: str,
        counter_evidence: str,
        *,
        bifrost: BifrostClient | None = None,
        tavily: Any = None,
    ) -> dict[str, Any]:
        """Open a challenge-and-review run.

        The original verdict remains immutable; the challenge opens a new
        review run with the user's counter-evidence. The new run can produce
        a revised conclusion with its own audit trail.
        """
        conv = self.store.get_conversation(conv_id)
        if not conv:
            raise ValueError(f"Conversation {conv_id} not found")

        claim = self.store.get_claim(claim_id)
        if not claim:
            raise ValueError(f"Claim {claim_id} not found")

        # Record original verdict for audit trail
        original_status = claim.get("status")
        original_reason = claim.get("reason_code")

        client = bifrost or BifrostClient(self.store)

        # Create a challenge run that includes the counter-evidence
        challenge_text = (
            f"Original claim: {claim['claim_text']}\n"
            f"Original verdict: {original_status} ({original_reason})\n"
            f"Counter-evidence provided: {counter_evidence}\n"
            f"Please evaluate whether the counter-evidence changes the verdict."
        )

        answer_in = AnswerIn(
            answer=challenge_text,
            prompt=f"Challenge review of claim: {claim['claim_text']}",
            generated_at=now(),
            generated_at_source="user_supplied",
            budget_mode="deep",
            skip_non_factual=False,
        )
        created = create_run(self.store, answer_in)
        run_id = created.run_id

        verifier = Verifier(self.store, run_id, bifrost=client, tavily=tavily)
        verifier.execute()

        # Compile evidence and synthesize
        context = compile_evidence_context(self.store, run_id, user_question=challenge_text)
        answer_text, validation = generate_grounded_answer(client, context)

        assistant_msg = self.store.create_message({
            "conversation_id": conv_id,
            "role": "assistant",
            "content": answer_text,
            "run_id": run_id,
            "citations": context.get("sources", []),
            "quality_metrics": validation.quality_metrics,
        })

        return {
            "challenge_status": "reviewed",
            "original_status": original_status,
            "original_reason": original_reason,
            "new_run_id": run_id,
            "assistant_message": assistant_msg,
            "context": context,
            "validation": validation,
        }
