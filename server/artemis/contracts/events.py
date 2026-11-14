"""The event envelope and the typed `data` for each event type (contracts.md §2)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, model_validator

from .approvals import Approval, FileAction, Finding

Priority = Literal["low", "normal", "high"]

EventType = Literal[
    "transcript.partial",
    "transcript.final",
    "task.state",
    "agent.message",
    "approval.requested",
    "approval.resolved",
    "file.changed",
    "run.result",
    "security.findings",
    "error",
]

# `awaiting_reply` is reserved and unused in the MVP (design A4).
TaskState = Literal[
    "queued",
    "working",
    "awaiting_reply",
    "awaiting_approval",
    "running",
    "succeeded",
    "failed",
    "canceled",
]


class Announce(BaseModel):
    text: str
    priority: Priority = "normal"


class TranscriptData(BaseModel):
    text: str
    client_request_id: str


class TaskStateData(BaseModel):
    state: TaskState
    reason: str | None = None


class AgentMessageData(BaseModel):
    text: str
    kind: Literal["answer", "question", "progress"]


class ApprovalResolvedData(BaseModel):
    approval_id: str
    decision: Literal["approve", "reject"]
    by: Literal["user", "invalidated"]


class FileChange(BaseModel):
    op_id: str
    path: str
    action: FileAction
    lines_added: int
    lines_removed: int


class RunResult(BaseModel):
    op_id: str
    preset: Literal["python"]
    exit_code: int | None  # None when the program was killed on timeout
    timed_out: bool
    duration_ms: int
    stdout_tail: str
    stderr_tail: str
    truncated: bool


class SecurityFindings(BaseModel):
    op_id: str
    tool: Literal["bandit", "semgrep"]
    findings: list[Finding]


class ErrorData(BaseModel):
    code: str
    message: str
    recovery: str  # one sentence the user can act on


DATA_MODELS: dict[str, type[BaseModel]] = {
    "transcript.partial": TranscriptData,
    "transcript.final": TranscriptData,
    "task.state": TaskStateData,
    "agent.message": AgentMessageData,
    "approval.requested": Approval,
    "approval.resolved": ApprovalResolvedData,
    "file.changed": FileChange,
    "run.result": RunResult,
    "security.findings": SecurityFindings,
    "error": ErrorData,
}


class EventDraft(BaseModel):
    """What a component hands to the event bus. The bus adds seq, session_id and at."""

    task_id: str | None = None
    type: EventType
    announce: Announce | None = None
    data: dict[str, Any] = {}

    @model_validator(mode="after")
    def _data_matches_type(self):
        DATA_MODELS[self.type].model_validate(self.data)
        return self


class Event(EventDraft):
    """The envelope on the wire. `seq` is the SSE id, strictly increasing per session."""

    seq: int
    session_id: str
    at: datetime
