"""The Python seams between owners (contracts.md §3). Each one gets a stub first."""

from collections.abc import Awaitable, Callable
from typing import Protocol

from .approvals import Approval, Decision
from .events import EventDraft, FileChange, RunResult
from .tasks import AgentOutcome, ProjectRef, TaskContext
from .tools import ToolResult

# The core's event bus. It adds seq, session_id and at, logs the event, and speaks `announce`.
Emit = Callable[[EventDraft], Awaitable[None]]


class ToolGateway(Protocol):
    async def call(self, task_id: str, name: str, args: dict) -> ToolResult: ...


class Agent(Protocol):
    async def run(self, task: TaskContext, tools: ToolGateway, emit: Emit) -> AgentOutcome: ...


class Approvals(Protocol):
    """Core: waits for POST /approvals/{aid}. The gateway emits the approval events itself."""

    async def request(self, task_id: str, approval: Approval) -> Decision: ...


class Runner(Protocol):
    """Infrastructure (contracts.md §3.4)."""

    def create_project(self, name: str) -> ProjectRef: ...

    def write_file(self, project: ProjectRef, path: str, content: str) -> FileChange: ...

    async def run(
        self, project: ProjectRef, entry: str, args: list[str], timeout_s: int = 10
    ) -> RunResult: ...
