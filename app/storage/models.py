from __future__ import annotations

from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class Message(BaseModel):
    role: Role
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    tool_name: str | None = None


class Conversation(BaseModel):
    id: str
    user_id: str
    messages: list[Message] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ToolCall(BaseModel):
    name: str
    arguments: dict
    result: str | None = None


class PermissionRequest(BaseModel):
    tool_name: str
    action: str
    resource: str
    tier: int = 1


class AgentResponse(BaseModel):
    content: str
    tool_calls: list[ToolCall] = []
    permission_requests: list[PermissionRequest] = []


# ── Phase 4: Self-Learning Loop Models ──


class Feedback(BaseModel):
    id: str
    message_id: int
    conversation_id: str
    user_id: str
    rating: str  # "up" or "down"
    comment: str = ""
    source: str = "local"  # "nexgai" or "local"
    agent_name: str | None = None
    created_at: str = ""


class LearningEntry(BaseModel):
    id: str
    entry_type: str  # "response_improvement", "prompt_refinement", "knowledge_expansion"
    source: str  # "nexgai" or "local"
    agent_name: str | None = None
    trigger_feedback_ids: str  # JSON array of feedback IDs
    original_query: str
    original_response: str
    suggested_improvement: str
    status: str = "pending"  # "pending", "approved", "rejected"
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    created_at: str = ""


class PromptVersion(BaseModel):
    id: str
    source: str  # "local"
    prompt_text: str
    is_active: bool = False
    learning_entry_id: str | None = None
    created_by: str
    created_at: str = ""


class SatisfactionSnapshot(BaseModel):
    snapshot_date: str  # YYYY-MM-DD
    source: str  # "nexgai", "local", or "all"
    total_feedback: int = 0
    thumbs_up: int = 0
    thumbs_down: int = 0
    satisfaction_pct: float = 0.0
