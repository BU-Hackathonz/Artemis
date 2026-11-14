"""Tasks: what the orchestrator hands the agent and what the agent returns (contracts.md §3.1)."""

from typing import Literal

from pydantic import BaseModel


class ProjectRef(BaseModel):
    slug: str
    root: str  # absolute path of the project folder


class TurnSummary(BaseModel):
    request: str
    outcome: str


class Style(BaseModel):
    """How the developer asked to be answered ("keep it brief", "visual mode"). Set by the
    conversation controller; the agent shapes its answer to it."""

    verbosity: Literal["brief", "normal", "detailed"] = "normal"
    focus: Literal["none", "visual", "code"] = "none"


class TaskContext(BaseModel):
    task_id: str
    request: str  # the final transcript or typed text
    project: ProjectRef | None = None  # None until one is created or selected
    history: list[TurnSummary] = []  # earlier tasks in this session, short
    style: Style = Style()


class Answered(BaseModel):
    text: str


class Failed(BaseModel):
    code: str
    message: str


class Canceled(BaseModel):
    pass


AgentOutcome = Answered | Failed | Canceled


class TaskRequest(BaseModel):
    """Body of POST /sessions/{sid}/tasks."""

    client_request_id: str
    source: Literal["voice", "keyboard"]
    text: str
