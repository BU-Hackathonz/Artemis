"""Sessions and tasks (contracts.md §1, §3.1, §5.2).

One session per app launch. A session has one event bus, one tool gateway (it holds the current
project) and one agent, and runs at most one task at a time. The core emits task.state queued /
succeeded / failed / canceled; the gateway emits the states in between.
"""

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from artemis.contracts import (
    Agent,
    AgentMessageData,
    Announce,
    Answered,
    Canceled,
    ErrorData,
    EventDraft,
    Failed,
    TaskContext,
    TaskRequest,
    TaskStateData,
    TranscriptData,
    TurnSummary,
)
from artemis.policy.gateway import ToolGateway

from .approvals import SessionApprovals
from .bus import EventBus

log = logging.getLogger(__name__)

MAX_HISTORY_TURNS = 10

# §5.2 spoken control words: whole utterance only, after lowercasing and dropping punctuation.
CANCEL_WORDS = {"cancel", "stop", "cancel that", "never mind", "nevermind"}
REPEAT_WORDS = {"repeat", "say that again"}
APPROVAL_WORDS = {"approve", "yes", "reject", "no"}

# "Still working" heartbeat (architecture.md risk 9: silence longer than about 3 s gets a cue).
HEARTBEAT_S = 3.0
HEARTBEAT_STATES = {"working", "running"}  # never queued, awaiting_approval or a final state
STILL_WORKING = "Still working."


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class TaskActive(Exception):
    def __init__(self, task_id: str):
        self.task_id = task_id


class Task:
    def __init__(self, task_id: str, request: TaskRequest):
        self.task_id = task_id
        self.request = request
        self.state = "queued"
        self.job: asyncio.Task | None = None
        self.heartbeat: asyncio.Task | None = None

    @property
    def active(self) -> bool:
        return self.state not in ("succeeded", "failed", "canceled")


class Session:
    def __init__(
        self,
        session_id: str,
        *,
        voice,
        runner,
        agent: Agent,
        projects_dir: Path,
        scan=None,
        log_dir: Path | None = None,
        on_project: Callable[[object], None] | None = None,
        heartbeat_s: float = HEARTBEAT_S,
    ):
        self.session_id = session_id
        self.voice = voice
        self.runner = runner
        self.heartbeat_s = heartbeat_s
        self._quiet_since = time.monotonic()  # last announcement or task state change
        self.bus = EventBus(
            session_id,
            speak=voice.announce if voice else None,
            log_path=(log_dir / f"{session_id}.jsonl") if log_dir else None,
        )
        self.approvals = SessionApprovals()
        self.gateway = ToolGateway(runner, self.approvals, self.emit, projects_dir=projects_dir, scan=scan)
        self.agent = agent
        self.tasks: dict[str, Task] = {}
        self._by_request: dict[str, str] = {}  # client_request_id -> task_id (idempotency)
        self.history: list[TurnSummary] = []
        self.reported: set[str] = set()  # client diagnostic codes already spoken this session
        self._on_project = on_project

    async def emit(self, draft: EventDraft) -> None:
        if draft.announce or draft.type == "task.state":
            # Something was said, or the state changed (e.g. back to working after an approval):
            # the heartbeat waits a full interval from here.
            self._quiet_since = time.monotonic()
        await self.bus.emit(draft)
        if draft.type == "task.state" and draft.task_id in self.tasks:
            self.tasks[draft.task_id].state = draft.data["state"]

    @property
    def active_task(self) -> Task | None:
        return next((t for t in self.tasks.values() if t.active), None)

    # ----- tasks -----

    async def submit(self, request: TaskRequest) -> Task:
        """Start a task. Idempotent per client_request_id. Raises TaskActive (§5.2)."""
        existing = self._by_request.get(request.client_request_id)
        if existing:
            return self.tasks[existing]
        active = self.active_task
        if active:
            raise TaskActive(active.task_id)
        task = Task(new_id("tsk"), request)
        self.tasks[task.task_id] = task
        self._by_request[request.client_request_id] = task.task_id
        await self._state(task.task_id, "queued", f"Working on: {request.text}", "normal")
        task.job = asyncio.create_task(self._run(task))
        task.heartbeat = asyncio.create_task(self._heartbeat(task))
        # However the job ends (even canceled before it starts), the heartbeat stops with it.
        task.job.add_done_callback(lambda _job: task.heartbeat.cancel())
        return task

    async def _heartbeat(self, task: Task) -> None:
        """While the task is working or running and nothing has been announced for heartbeat_s,
        give a quiet "still working" cue, and repeat it every heartbeat_s until something else is
        announced or the state leaves working/running. Never raises into the task."""
        while task.active:
            wait = self._quiet_since + self.heartbeat_s - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
                continue
            if task.state in HEARTBEAT_STATES:
                try:
                    await self._still_working(task.task_id)
                except Exception:  # a cue failing must never stop the task
                    log.exception("still-working cue failed")
            self._quiet_since = time.monotonic()  # next check a full interval later

    async def _still_working(self, task_id: str) -> None:
        """voice/earcons.py only has "listening" and "stopped", and both mean the mic, so reusing
        either would mislead. Use voice.working_cue() if a voice ever offers one; until then a
        low-priority progress line, which the Announcer drops while speech is busy, so it never
        talks over anything."""
        cue = getattr(self.voice, "working_cue", None)
        if callable(cue):
            result = cue()
            if asyncio.iscoroutine(result):
                await result
            return
        await self.emit(
            EventDraft(
                task_id=task_id,
                type="agent.message",
                announce=Announce(text=STILL_WORKING, priority="low"),
                data=AgentMessageData(text=STILL_WORKING, kind="progress").model_dump(),
            )
        )

    async def _run(self, task: Task) -> None:
        tid = task.task_id
        context = TaskContext(
            task_id=tid,
            request=task.request.text,
            project=self.gateway.project,
            history=self.history[-MAX_HISTORY_TURNS:],
        )
        project_before = self.gateway.project
        outcome_text = ""
        try:
            # Silent queued -> working: the agent is thinking now, so the heartbeat covers the
            # first model call too (the gateway only reports states around tool calls).
            await self._state(tid, "working")
            outcome = await self.agent.run(context, self.gateway, self.emit)
        except asyncio.CancelledError:
            outcome = Canceled()
        except Exception as exc:  # an agent crash is spoken, never silent
            log.exception("agent crashed")
            outcome = Failed(code="agent_crashed", message=type(exc).__name__)

        if isinstance(outcome, Answered):
            outcome_text = outcome.text
            await self._state(tid, "succeeded")
        elif isinstance(outcome, Canceled):
            outcome_text = "canceled"
            await self._state(tid, "canceled", "Canceled.", "high")
        else:
            outcome_text = f"failed: {outcome.code}"
            await self.emit(
                EventDraft(
                    task_id=tid,
                    type="error",
                    announce=Announce(
                        text=f"The task failed. {outcome.message or outcome.code}. You can try again.",
                        priority="high",
                    ),
                    data=ErrorData(
                        code=outcome.code,
                        message=outcome.message or outcome.code,
                        recovery="Try the request again, or say it differently.",
                    ).model_dump(),
                )
            )
            await self._state(tid, "failed", reason=outcome.code)

        self.history.append(TurnSummary(request=task.request.text, outcome=outcome_text[:300]))
        if self.gateway.project is not None and self.gateway.project != project_before and self._on_project:
            try:
                self._on_project(self.gateway.project)
            except Exception:
                log.exception("on_project hook failed")

    async def cancel(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task or not task.active or not task.job:
            return False
        task.job.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task.job), timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        return True

    async def _state(
        self, task_id: str, state: str, text: str | None = None, priority="low", reason: str | None = None
    ) -> None:
        await self.emit(
            EventDraft(
                task_id=task_id,
                type="task.state",
                announce=Announce(text=text, priority=priority) if text else None,
                data=TaskStateData(state=state, reason=reason).model_dump(),
            )
        )

    # ----- voice (§5.1, §5.2) -----

    async def spoken(self, text: str, client_request_id: str) -> None:
        """Handle a final transcript: control words, the one-task rule, or a new task."""
        await self.emit(
            EventDraft(
                type="transcript.final",
                announce=Announce(text=f"Heard: {text}", priority="normal") if text else None,
                data=TranscriptData(text=text, client_request_id=client_request_id).model_dump(),
            )
        )
        if not text.strip():
            # An accidental tap of push-to-talk records nothing: stay quiet (Yaovi's review on #20).
            # The stop earcon already told the user the mic closed.
            return
        words = normalize(text)
        active = self.active_task
        if words in CANCEL_WORDS:
            if active:
                await self.cancel(active.task_id)
            else:
                await self.say(None, "Nothing to cancel.")
            return
        if words in REPEAT_WORDS:
            await self.voice.repeat_last()
            return
        if words in APPROVAL_WORDS and active and active.state == "awaiting_approval":
            await self.say(
                active.task_id,
                "Use the keyboard to decide: Enter on the dialog to approve, Escape to reject.",
                "high",
            )
            return
        if active:
            await self.error(
                "task_active",
                "Still working on the last request.",
                "Say cancel to stop it, or wait.",
                task_id=active.task_id,
            )
            return
        await self.submit(TaskRequest(client_request_id=client_request_id, source="voice", text=text))

    async def say(self, task_id: str | None, text: str, priority="normal") -> None:
        await self.emit(
            EventDraft(
                task_id=task_id,
                type="agent.message",
                announce=Announce(text=text, priority=priority),
                data=AgentMessageData(text=text, kind="answer").model_dump(),
            )
        )

    async def error(self, code: str, message: str, recovery: str, task_id: str | None = None) -> None:
        await self.emit(
            EventDraft(
                task_id=task_id,
                type="error",
                announce=Announce(text=f"{message} {recovery}", priority="high"),
                data=ErrorData(code=code, message=message, recovery=recovery).model_dump(),
            )
        )

    async def voice_status(self, voice_ready: bool, screen_reader: str | None) -> None:
        await self.emit(
            EventDraft(type="voice.status", data={"voice_ready": voice_ready, "screen_reader": screen_reader})
        )
