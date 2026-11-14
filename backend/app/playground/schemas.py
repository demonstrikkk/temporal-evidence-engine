"""The playground's request contract.

Two things are deliberate here.

**The key is not in the model's repr.** A `PlaygroundChatIn` could otherwise be
printed by a traceback, a debugger or a `log.exception` in some future code
path, and its repr would contain a live API key. `repr=False` makes that
impossible rather than unlikely, which is the only version of the guarantee
worth having.

**The transcript must end on the user's turn.** Half a conversation — two
assistant messages in a row, or a trailing tool result — is not a request any
provider accepts consistently, and the frontend builds this list from state it
owns. Refusing it here keeps the failure at the boundary instead of turning it
into a provider-shaped error the user cannot interpret.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Role = Literal["system", "user", "assistant"]
Source = Literal["endpoint", "gateway"]


class PlaygroundMessage(BaseModel):
    role: Role
    content: str = Field(min_length=1, max_length=16_000)


class PlaygroundChatIn(BaseModel):
    model_config = ConfigDict(repr=False)

    #: Whose credential pays for the call. `endpoint` is the default because it
    #: is what this API did before the gateway existed: a caller that names no
    #: source keeps exactly the behaviour it had. The page chooses the
    #: gateway explicitly, because "use the deployment's key" is a decision, not
    #: a fallback — and a request must never silently spend the operator's money.
    #: `gateway` routes through Bifrost with this deployment's virtual key, the
    #: same route a verification takes, and needs no base URL from the caller.
    source: Source = "endpoint"
    base_url: str = Field(default="", max_length=400)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(default="", max_length=400)
    messages: list[PlaygroundMessage] = Field(min_length=1, max_length=16)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_output_tokens: int = Field(default=900, ge=16, le=4096)
    allow_tools: bool = True
    max_tool_calls: int = Field(default=2, ge=0, le=5)

    @field_validator("api_key")
    @classmethod
    def _strip_key(cls, value: str) -> str:
        # A pasted key routinely carries a trailing newline from the clipboard,
        # and an Authorization header with "\n" in it is rejected by every
        # provider with a message about the wrong thing.
        return value.strip()

    @model_validator(mode="after")
    def _endpoint_needs_a_url(self) -> "PlaygroundChatIn":
        # A gateway call takes its URL from configuration. Requiring one anyway
        # would make the default path fail validation for a field it never
        # reads, which is how a form grows an input nobody needs.
        if self.source == "endpoint" and len(self.base_url.strip()) < 8:
            raise ValueError("base_url is required when source is 'endpoint'")
        return self

    @field_validator("messages")
    @classmethod
    def _ends_on_the_user(
        cls, value: list[PlaygroundMessage]
    ) -> list[PlaygroundMessage]:
        if value[-1].role != "user":
            raise ValueError("the last message must be from the user")
        return value
