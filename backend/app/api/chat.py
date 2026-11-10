"""Conversational research API endpoints.

Provides multi-turn research conversations, evidence-grounded answer synthesis,
and interactive claim challenge workflows.
"""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import require_api_key
from app.conversation.manager import ConversationManager
from app.core.errors import not_found, validation
from app.verify.runtime import get_store

router = APIRouter(prefix="/api/chat", tags=["chat"],
                   dependencies=[Depends(require_api_key)])


class ConversationCreateIn(BaseModel):
    title: str = Field(default="New Research Session", max_length=200)
    budget_mode: Literal["balanced", "conserve", "deep", "offline"] = "balanced"


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=10000)


class ChallengeIn(BaseModel):
    counter_evidence: str = Field(min_length=1, max_length=10000)
    claim_id: str | None = None


def _manager() -> ConversationManager:
    return ConversationManager(get_store())


@router.post("/conversations")
async def create_conversation(body: ConversationCreateIn) -> dict[str, Any]:
    mgr = _manager()
    conv = await asyncio.to_thread(mgr.create_conversation, body.title, body.budget_mode)
    return {"conversation": conv}


@router.get("/conversations")
async def list_conversations(limit: int = Query(default=50, ge=1, le=100)) -> dict[str, Any]:
    mgr = _manager()
    rows = await asyncio.to_thread(mgr.list_conversations, limit)
    return {"conversations": rows}


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str) -> dict[str, Any]:
    mgr = _manager()
    conv = await asyncio.to_thread(mgr.get_conversation, conv_id)
    if not conv:
        raise not_found("conversation", conv_id)
    messages = await asyncio.to_thread(mgr.list_messages, conv_id)
    return {
        "conversation": conv,
        "messages": messages,
    }


@router.post("/conversations/{conv_id}/messages")
async def post_message(conv_id: str, body: MessageIn) -> dict[str, Any]:
    mgr = _manager()
    conv = await asyncio.to_thread(mgr.get_conversation, conv_id)
    if not conv:
        raise not_found("conversation", conv_id)

    turn = await asyncio.to_thread(mgr.process_turn, conv_id, body.content)
    return {
        "user_message": turn["user_message"],
        "assistant_message": turn["assistant_message"],
        "evidence_context": turn["context"],
        "quality_metrics": turn["validation"].quality_metrics,
        "intent": turn["intent"],
    }


@router.post("/conversations/{conv_id}/messages/{msg_id}/challenge")
async def challenge_message(conv_id: str, msg_id: str, body: ChallengeIn) -> dict[str, Any]:
    mgr = _manager()
    conv = await asyncio.to_thread(mgr.get_conversation, conv_id)
    if not conv:
        raise not_found("conversation", conv_id)

    prompt = f"Challenge against previous findings: {body.counter_evidence}"
    turn = await asyncio.to_thread(mgr.process_turn, conv_id, prompt)
    return {
        "challenge_status": "reviewed",
        "assistant_message": turn["assistant_message"],
        "quality_metrics": turn["validation"].quality_metrics,
    }
