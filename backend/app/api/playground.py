"""Playground router: ask a model, then scan what it said.

Deliberately narrow. Three routes and no persistence: the playground writes no
run, no answer and no model call. The scan is a separate, explicit POST to
`/api/verify/runs`, which is the same pipeline every other answer goes through —
so the playground cannot become a second, weaker verification path.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import require_api_key
from app.playground import client as playground_client
from app.playground.presets import PRESETS, gateway_preset
from app.playground.schemas import PlaygroundChatIn

router = APIRouter(prefix="/api/playground", tags=["playground"],
                   dependencies=[Depends(require_api_key)])


@router.get("/presets")
async def presets() -> dict[str, Any]:
    """Endpoints to offer, and whether the search tool can run at all.

    The UI needs the second fact before it draws the toggle. Offering a tool
    this deployment cannot execute would produce a model that asks for search
    and a receipt that says "no tool", which reads as a bug rather than as
    configuration.

    The gateway preset comes first when this deployment can reach it. Order is
    the recommendation: the default path is the one the deployment already
    pays for and has already proven, and a reader should not have to type a
    key to see the product work.
    """
    gateway = gateway_preset()
    return {
        "presets": [gateway, *PRESETS] if gateway else PRESETS,
        "tools_available": playground_client._tavily_enabled(),
        "note": (
            "The default route is this deployment's gateway, the same one the "
            "verification uses. Bring your own endpoint for a model this "
            "deployment has never heard of. Either way the playground writes "
            "nothing; scanning the answer starts a normal verification run."
        ),
    }


@router.post("/chat")
async def chat(body: PlaygroundChatIn) -> dict[str, Any]:
    """One conversation turn, with the tool receipt that produced it."""
    return await playground_client.chat(body)
