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
    "voice.status",
    "voice.activity",
    "agent.plan",
    "ring.cue",
    "desktop.action",
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
    text: str  # always the raw transcript
    client_request_id: str
    # How the core handled it: "request", or a built-in phrase such as "status". None when the
    # conversation controller is off.
    intent: str | None = None


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


class VoiceStatusData(BaseModel):
    voice_ready: bool
    screen_reader: Literal["nvda", "orca"] | None = None
    engine: str | None = None  # the speaker that would say the next line (docs/contracts.md section 7)


class VoiceActivityData(BaseModel):
    """What the voice is doing right now, from real transitions only (docs/contracts.md section 2)."""

    state: Literal["idle", "listening", "transcribing", "speaking"]
    engine: str | None = None  # who is speaking: elevenlabs, sapi, nvda (an estimate), or null


class PlanStepData(BaseModel):
    say: str
    action: Literal["think", "read", "write", "run", "open", "type", "explain"]
    target: str | None = None


class AgentPlanData(BaseModel):
    """The agent's plan for the request, before it acts (contracts.md §10, Proposed)."""

    goal: str
    steps: list[PlanStepData]


class RingCueData(BaseModel):
    """A plan step that is really happening now: sent by the gate on each tool call, never from the
    plan alone, so the ring only shows what Artemis is actually doing (§10)."""

    step: int | None = None  # index into the plan's steps, when a step matches
    action: Literal["think", "read", "write", "run", "open", "type", "explain", "done"]
    target: str | None = None  # "editor", "terminal", "file", "browser", an app name, or a path's name


class DesktopActionData(BaseModel):
    """An approved desktop action the island performs (only the shell can type on Wayland; §9).
    The island answers with POST /desktop/{op_id}/result."""

    op_id: str
    # focus: before the approval, report which window has the keyboard focus (no typing);
    # type / keys: type into that same window only, and refuse if the focus moved.
    kind: Literal["focus", "type", "keys"]
    text: str = ""
    window: str | None = None  # type / keys: the window the developer approved, as focus reported it


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
    "voice.status": VoiceStatusData,
    "voice.activity": VoiceActivityData,
    "agent.plan": AgentPlanData,
    "ring.cue": RingCueData,
    "desktop.action": DesktopActionData,
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
