"""In-memory fakes for the interfaces the gateway depends on.

Used by the tests here, and usable by the core and the client for a walking skeleton before
the real runner and approvals exist.
"""

import inspect
from collections.abc import Callable
from pathlib import Path

from artemis.contracts import Approval, Decision, EventDraft, FileChange, ProjectRef, RunResult


class EventLog:
    """An `Emit` that records every event."""

    def __init__(self):
        self.events: list[EventDraft] = []

    async def __call__(self, event: EventDraft) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [e.type for e in self.events]

    def of(self, type: str) -> list[EventDraft]:
        return [e for e in self.events if e.type == type]

    def spoken(self) -> list[str]:
        return [e.announce.text for e in self.events if e.announce]


class FakeApprovals:
    """Answers each request from a script, then approves. A step is a Decision or a function
    of the Approval (sync or async) that returns one."""

    def __init__(self, script: list[Decision | Callable] | None = None):
        self.script = list(script or [])
        self.requests: list[Approval] = []

    async def request(self, task_id: str, approval: Approval) -> Decision:
        self.requests.append(approval)
        step = self.script.pop(0) if self.script else Decision.approve
        if callable(step):
            step = step(approval)
            if inspect.isawaitable(step):
                step = await step
        return step


class FakeRunner:
    """Writes real files under `projects_dir`; runs nothing and returns a scripted result."""

    def __init__(self, projects_dir: Path, result: RunResult | None = None):
        self.projects_dir = Path(projects_dir)
        self.result = result
        self.runs: list[tuple[str, list[str], int]] = []
        self._ops = 0

    def _op(self) -> str:
        self._ops += 1
        return f"op_{self._ops}"

    def create_project(self, name: str) -> ProjectRef:
        root = self.projects_dir / name
        root.mkdir(parents=True)
        return ProjectRef(slug=name, root=str(root))

    def write_file(self, project: ProjectRef, path: str, content: str) -> FileChange:
        target = Path(project.root, path)
        old = target.read_text(encoding="utf-8").splitlines() if target.exists() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))  # bytes, so newlines aren't translated
        new = content.splitlines()
        return FileChange(
            op_id=self._op(),
            path=path,
            action="created" if old is None else "modified",
            lines_added=len(new) if old is None else len([line for line in new if line not in old]),
            lines_removed=0 if old is None else len([line for line in old if line not in new]),
        )

    async def run(
        self, project: ProjectRef, entry: str, args: list[str], timeout_s: int = 10
    ) -> RunResult:
        self.runs.append((entry, args, timeout_s))
        result = self.result or RunResult(
            op_id="",
            preset="python",
            exit_code=0,
            timed_out=False,
            duration_ms=64,
            stdout_tail="Hello, World!\n",
            stderr_tail="",
            truncated=False,
        )
        return result.model_copy(update={"op_id": self._op()})
