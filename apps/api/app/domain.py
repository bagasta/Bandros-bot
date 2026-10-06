from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BotStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class RunStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED = "failed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class RiskClass(StrEnum):
    READ_ONLY = "read_only"
    LOCAL_WRITE = "local_write"
    EXTERNAL_WRITE = "external_write"
    DESTRUCTIVE = "destructive"
    SENSITIVE = "sensitive"


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CreateBot(APIModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2_000)
    instructions: str = Field(default="", max_length=10_000)
    model: str | None = Field(default=None, max_length=200)


class UpdateBot(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2_000)
    instructions: str | None = Field(default=None, max_length=10_000)
    model: str | None = Field(default=None, max_length=200)


class Bot(APIModel):
    id: UUID
    name: str
    description: str
    instructions: str
    model: str | None
    status: BotStatus
    created_at: datetime
    updated_at: datetime


class MessageInput(APIModel):
    content: str = Field(min_length=1, max_length=50_000)
    model: str | None = Field(default=None, max_length=200)
    attachments: list[dict[str, Any]] = Field(default_factory=list, max_length=10)


class Message(APIModel):
    id: UUID
    conversation_id: UUID
    role: str
    content: str
    model: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class Run(APIModel):
    id: UUID
    bot_id: UUID
    conversation_id: UUID
    status: RunStatus
    prompt: str
    model: str
    error: str | None
    continuation: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    stop_requested: bool = False
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    heartbeat_at: datetime | None = None


class RunEvent(APIModel):
    id: int
    run_id: UUID
    type: str
    payload: dict[str, Any]
    created_at: datetime


class MessageEditInput(APIModel):
    content: str = Field(min_length=1, max_length=50_000)
    model: str | None = Field(default=None, max_length=200)


class RegenerateInput(APIModel):
    model: str | None = Field(default=None, max_length=200)


class ApprovalProposal(APIModel):
    tool_name: str = Field(min_length=1, max_length=128)
    risk_class: RiskClass
    reason: str = Field(min_length=1, max_length=2_000)
    payload: dict[str, Any] = Field(default_factory=dict)


class Approval(APIModel):
    id: UUID
    run_id: UUID
    tool_name: str
    risk_class: RiskClass
    reason: str
    payload: dict[str, Any]
    status: ApprovalStatus
    requested_at: datetime
    decided_at: datetime | None


class Memory(APIModel):
    id: UUID
    bot_id: UUID
    kind: str
    content: str
    created_at: datetime


class ChatGPTConnection(APIModel):
    connected: bool
    email: str | None = None
    scope: str | None = None
    expires_at: datetime | None = None


class SkillInput(APIModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2_000)
    content: str = Field(min_length=1, max_length=20_000)


class Skill(APIModel):
    id: UUID
    name: str
    description: str
    content: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AssignSkill(APIModel):
    skill_id: UUID
    config: dict[str, Any] = Field(default_factory=dict)


class JobInput(APIModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5_000)
    priority: str = Field(default="normal", pattern="^(low|normal|high)$")
    assignee_bot_id: UUID | None = None


class JobUpdate(APIModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5_000)
    priority: str | None = Field(default=None, pattern="^(low|normal|high)$")
    status: str | None = Field(default=None, pattern="^(open|in_progress|blocked|done)$")
    assignee_bot_id: UUID | None = None


class Job(APIModel):
    id: UUID
    title: str
    description: str
    status: str
    priority: str
    assignee_bot_id: UUID | None
    created_by_bot_id: UUID | None
    created_at: datetime
    updated_at: datetime


class GroupInput(APIModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2_000)
    member_bot_ids: list[UUID] = Field(default_factory=list, max_length=20)


class WorkGroup(APIModel):
    id: UUID
    name: str
    description: str
    members: list[Bot] = Field(default_factory=list)
    created_at: datetime


class GroupMemberInput(APIModel):
    bot_id: UUID


class GroupActivity(APIModel):
    bot_id: UUID
    name: str
    status: str


class BotActivity(APIModel):
    working: bool
    error: str | None = None
    approvals: list[Approval] = Field(default_factory=list)


class GroupMessageInput(APIModel):
    content: str = Field(min_length=1, max_length=50_000)


class GroupMessage(APIModel):
    id: UUID
    group_id: UUID
    sender_type: str
    sender_bot_id: UUID | None
    content: str
    created_at: datetime


class PluginInput(APIModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=8, max_length=500)
    token: str | None = Field(default=None, max_length=2_000)


class Plugin(APIModel):
    id: UUID
    name: str
    url: str
    description: str
    tools: list[str] = Field(default_factory=list)
    created_at: datetime


class ClawHubQuery(APIModel):
    q: str = Field(min_length=1, max_length=120)


class ClawHubListing(APIModel):
    slug: str
    owner_handle: str
    name: str
    summary: str
    url: str


class ClawHubInstall(APIModel):
    slug: str = Field(min_length=1, max_length=120)
    owner_handle: str = Field(min_length=1, max_length=80)
    bot_id: UUID | None = None


class HandoffInput(APIModel):
    target_bot_id: UUID
    task: str = Field(min_length=1, max_length=20_000)


class Handoff(APIModel):
    id: UUID
    source_bot_id: UUID
    target_bot_id: UUID
    parent_run_id: UUID | None
    child_run_id: UUID | None
    task: str
    status: str
    result: str | None
    created_at: datetime
    completed_at: datetime | None
