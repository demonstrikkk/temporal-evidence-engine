"""Intent classification and conversation context routing.

Distinguishes between new topic research, conversational follow-up reusing prior
evidence, explicit re-verification requests, and verdict challenges.
"""
from __future__ import annotations

import re
from typing import Literal

from app.conversation.memory import StructuredMemory

IntentType = Literal["new_topic", "follow_up", "reverify", "challenge"]

_REVERIFY_PATTERNS = re.compile(
    r"\b(verify\s+again|reverify|recheck|check\s+again|fresh\s+research|check\s+live|refresh\s+sources)\b",
    re.I,
)
_CHALLENGE_PATTERNS = re.compile(
    r"\b(that('?s|\s+is)\s+wrong|i\s+disagree|incorrect|dispute|counter\s*evidence|false\s+finding|challenge)\b",
    re.I,
)
_PRONOUNS_FOLLOWUP = re.compile(
    r"\b(it|its|they|their|that|before|earlier|previous|what\s+about|how\s+about|also)\b",
    re.I,
)


def classify_intent(message_text: str, memory: StructuredMemory) -> IntentType:
    """Classify the user turn intent using deterministic cues and memory state."""
    text = message_text.strip().lower()

    if _REVERIFY_PATTERNS.search(text):
        return "reverify"

    if _CHALLENGE_PATTERNS.search(text):
        return "challenge"

    # If there is prior active context and message uses pronouns or follow-up cues
    if memory.active_entities or memory.last_run_id:
        if _PRONOUNS_FOLLOWUP.search(text) or len(text.split()) <= 6:
            return "follow_up"
        # Check if any active entity name appears in text
        for entity in memory.active_entities:
            if entity.lower() in text:
                return "follow_up"

    return "new_topic"
