"""Conversation threads for the resume-generation agent.

LangGraph's Mongo checkpointer owns its own collections
(`agent_checkpoints`, `agent_checkpoint_writes`) and stores opaque graph
state. This module adds the *application's* view on top: a listable,
user-facing thread with a title and a status, so the UI can show "3
conversations in progress" without deserializing graph checkpoints.

Separating the two matters because checkpoint format is LangGraph's to
change, and we do not want product features reading it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from app.models.base import ApiModel, MongoModel, PyObjectId
from app.models.bullet import BulletDraft, XYZViolation


class ThreadKind(StrEnum):
    RESUME_GENERATION = "resume_generation"
    BULLET_REPAIR = "bullet_repair"
    RESUME_TAILORING = "resume_tailoring"
    COVER_LETTER = "cover_letter"


class ThreadStatus(StrEnum):
    ACTIVE = "active"
    AWAITING_USER = "awaiting_user"  # graph interrupted for input
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    FAILED = "failed"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class AgentThread(MongoModel):
    """Collection: `agent_threads`."""

    user_id: PyObjectId
    kind: ThreadKind
    thread_id: str = Field(
        description="LangGraph thread id (the checkpointer's configurable.thread_id). "
        "Unique; this is the join key into checkpoint state."
    )
    resume_id: PyObjectId | None = None
    profile_entry_id: str | None = Field(
        default=None, description="Experience entry under discussion, when scoped."
    )

    status: ThreadStatus = ThreadStatus.ACTIVE
    title: str = Field(default="New conversation", max_length=160)
    message_count: int = Field(default=0, ge=0)
    last_message_at: datetime | None = None

    bullets_produced: int = Field(default=0, ge=0)
    bullets_flagged: int = Field(
        default=0,
        ge=0,
        description="Bullets that exhausted the repair budget. A rising ratio here is "
        "the signal that prompts or the model need attention.",
    )
    total_tokens: int = Field(default=0, ge=0, description="Cost attribution per thread.")


# --- API contracts ---------------------------------------------------------


class ChatMessage(ApiModel):
    role: MessageRole
    content: str
    at: datetime | None = None


class GenerateStartRequest(ApiModel):
    """Open a resume-generation conversation."""

    resume_id: PyObjectId | None = Field(
        default=None, description="Omit to have the agent create a new resume."
    )
    profile_entry_id: str | None = Field(
        default=None, description="Scope the conversation to one experience entry."
    )
    target_job_id: PyObjectId | None = None
    seed_notes: str | None = Field(
        default=None,
        max_length=8000,
        description="Raw brain-dump to mine for achievements. If omitted, the agent "
        "falls back to the entry's stored `raw_notes`.",
    )


class GenerateTurnRequest(ApiModel):
    """Continue an existing conversation."""

    message: str = Field(min_length=1, max_length=4000)


class BulletProposal(ApiModel):
    """A candidate bullet returned mid-conversation.

    Carries `violations` even when accepted, so the UI can show *why* a bullet
    needed a repair pass. Surfacing the reasoning teaches the user to write
    better raw notes, which improves every later generation.
    """

    draft: BulletDraft
    text: str = Field(description="Assembled XYZ text.")
    violations: list[XYZViolation] = Field(default_factory=list)
    repair_attempts: int = Field(default=0, ge=0)
    accepted: bool = Field(description="True when the deterministic validator passed.")
    bullet_id: PyObjectId | None = Field(
        default=None, description="Persisted proposal ID, present once generation is complete."
    )
    decision: Literal["accepted", "revised", "rejected"] | None = None


class ProposalDecision(ApiModel):
    """User decision for a persisted generated proposal."""

    action: str = Field(pattern="^(accept|reject|revise)$")
    draft: BulletDraft | None = None


class GenerateTurnResponse(ApiModel):
    thread_id: str
    status: ThreadStatus
    assistant_message: str = Field(description="Reply to render in the chat pane.")
    question: str | None = Field(
        default=None,
        description="Set when the graph interrupted to ask for a missing metric. The UI "
        "should focus the input and show this prominently.",
    )
    proposals: list[BulletProposal] = Field(default_factory=list)
    needs_input: bool = Field(
        default=False, description="Whether the graph is paused awaiting a reply."
    )
