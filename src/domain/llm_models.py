from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# --- LLM Message ---


class MessageRole(str, Enum):
    """Roles in a multi-turn LLM conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class LLMMessage(BaseModel):
    """A single message in a multi-turn conversation."""

    role: MessageRole
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_calls: list['ToolCall'] = Field(default_factory=list)
    thinking: str | None = None


# --- Tool Definitions & Calls ---


class ToolDefinition(BaseModel):
    """Schema for a tool the LLM can invoke."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)


class ToolCall(BaseModel):
    """A structured tool invocation returned by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


# --- LLM Request / Response ---


class FinishReason(str, Enum):
    """Why the LLM stopped generating."""

    STOP = "stop"
    TOOL_CALL = "tool_call"
    LENGTH = "length"
    ERROR = "error"
    CONTENT_FILTER = "content_filter"
    UNKNOWN = "unknown"


class LLMRequest(BaseModel):
    """A generic request to an LLM provider.

    For single-turn convenience, set ``prompt`` alone.
    For multi-turn conversations, populate ``messages``.
    If both are provided, ``messages`` takes precedence and ``prompt`` is
    ignored by the provider adapter.
    """

    prompt: str | None = None
    messages: list[LLMMessage] = Field(default_factory=list)
    tools: list[ToolDefinition] = Field(default_factory=list)
    system_prompt: str | None = None
    thinking_mode: bool = False


class LLMResponse(BaseModel):
    """A generic response from an LLM provider."""

    content: str | None = None
    thinking: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: FinishReason = FinishReason.UNKNOWN
    error: str | None = None
    model_name: str
    token_count: int | None = None
    prompt_eval_count: int | None = None
    eval_count: int | None = None
    latency_ms: float | None = None
