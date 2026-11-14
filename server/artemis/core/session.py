"""Sessions and tasks (contracts.md §1, §3.1, §5.2).

One session per app launch. A session has one event bus, one tool gateway (it holds the current
project) and one agent, and runs at most one task at a time. The core emits task.state queued /
succeeded / failed / canceled; the gateway emits the states in between.
"""

import asyncio
import inspect
import logging
import os
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
from artemis.conversation import Controller
from artemis.conversation.controller import NVDA_RATE_LINE
from artemis.policy.gateway import ToolGateway

from .approvals import SessionApprovals
from .bus import EventBus

log = logging.getLogger(__name__)

# What the core says for each rate status the voice reports (voice.speech.RateStatus, compared as plain
# strings so the core never imports the voice). "no_output" uses the controller's own fallback line.
RATE_LINES = {
    "faster": "Speaking faster.",
    "slower": "Speaking slower.",
    "at_max": "That's the fastest I go.",
    "at_min": "That's the slowest I go.",
    "nvda": NVDA_RATE_LINE,
}

MAX_HISTORY_TURNS = 10

# §5.2 spoken control words: whole utterance only, after lowercasing and dropping punctuation.
CANCEL_WORDS = {"cancel", "stop", "cancel that", "never mind", "nevermind"}
REPEAT_WORDS = {"repeat", "say that again"}
APPROVAL_WORDS = {"approve", "yes", "reject", "no"}

# "Still working" heartbeat (architecture.md risk 9: silence longer than about 3 s gets a cue).
HEARTBEAT_S = 3.0
HEARTBEAT_STATES = {"working", "running"}  # never queued, awaiting_approval or a final state
STILL_WORKING = "Still working."
EMPTY_PAIR_S = 1.5  # a second empty transcript this soon after the first is the same tap


def spoken_error(message: str, recovery: str) -> str:
    """The spoken line for an error: the message and the recovery hint, each sentence ended.

    The two are joined with a space, so a message without its own full stop would run on into
    the hint; end it here unless it already ends with ., ? or !.
    """
    message = message.rstrip()
    if message and message[-1] not in ".?!":
        message += "."
    return f"{message} {recovery}".strip()


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _default_earcon() -> Callable[[str], float] | None:
    """voice/earcons.play, imported only when result earcons are on (the voice group may be absent)."""
    try:
        from artemis.voice.earcons import play
    except ImportError:
        log.warning("ARTEMIS_RESULT_EARCONS=on, but the voice group is not installed; no result earcons")
        return None
    return play


def _result_cue(draft: EventDraft) -> str | None:
    """The earcon for a spoken run result or approval request; None for every other event."""
    if draft.type == "run.result":
        timed_out = draft.data.get("timed_out")
        return "failure" if timed_out or draft.data.get("exit_code") != 0 else "success"
    if draft.type == "approval.requested":
        return "approval"
    return None


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
        self.cancel_reported = False  # the job already said what its cancel stopped

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
        conversation: bool | None = None,
        result_earcons: bool | None = None,
        earcon: Callable[[str], float] | None = None,
    ):
        self.session_id = session_id
        self.voice = voice
        self.runner = runner
        self.heartbeat_s = heartbeat_s
        self._quiet_since = time.monotonic()  # last announcement or task state change
        self._last_empty = float("-inf")  # when the last empty transcript arrived
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
        self.reported: set[tuple[str, str | None]] = set()  # (code, shortcut) already spoken
        self.greeted = False  # the ready line (POST /sessions/{sid}/ready) is spoken once
        self._on_project = on_project
        # contracts.md §5.4, off by default: None means today's §5.2 rules, unchanged.
        if conversation is None:
            conversation = os.environ.get("ARTEMIS_CONVERSATION", "off").lower() == "on"
        self.conversation: Controller | None = Controller() if conversation else None
        # ARTEMIS_RESULT_EARCONS, off by default (contracts.md 5.5): a short tone just before a run
        # result or an approval request is spoken. The words are always said; never while listening.
        if result_earcons is None:
            result_earcons = os.environ.get("ARTEMIS_RESULT_EARCONS", "off").lower() == "on"
        self._earcon: Callable[[str], float] | None = (earcon or _default_earcon()) if result_earcons else None

    async def emit(self, draft: EventDraft) -> None:
        if draft.announce or draft.type == "task.state":
            # Something was said, or the state changed (e.g. back to working after an approval):
            # the heartbeat waits a full interval from here.
            self._quiet_since = time.monotonic()
        task = self.tasks.get(draft.task_id) if draft.task_id else None
        if draft.announce and task and task.job and task.job is asyncio.current_task() and task.job.cancelling():
            # Said by the task itself while it is being canceled: the gateway's report of what
            # the cancel stopped ("Canceled. Nothing was written.", "Canceled while running main.py.").
            task.cancel_reported = True
        if self._earcon and draft.announce and not getattr(self.voice, "listening", False):
            cue = _result_cue(draft)
            if cue:
                try:
                    self._earcon(cue)  # plays in the background; the spoken line follows on the bus
                except Exception:  # a cue failing must never stop the event
                    log.exception("result earcon %s failed", cue)
        await self.bus.emit(draft)
        if draft.type == "task.state" and draft.task_id in self.tasks:
            self.tasks[draft.task_id].state = draft.data["state"]
        if self.conversation:
            try:
                self.conversation.observe(draft)
            except Exception:  # a controller bug must never break the event stream
                log.exception("conversation controller failed to observe %s", draft.type)

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
        if self.conversation:
            self.conversation.begin(task.task_id, request.text)
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
        """voice/earcons.py has "listening" and "stopped" (both mean the mic) and the result cues
        "success", "failure" and "approval", none of which means progress, so none is reused here.
        Use voice.working_cue() if a voice ever offers one; until then a
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
        if self.conversation:
            context.style = self.conversation.style
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
            # A second high-priority "Canceled." would cut off the report of what was already
            # applied (contracts.md §1), so it is only said when nothing else was.
            text = None if task.cancel_reported else "Canceled."
            await self._state(tid, "canceled", text, "high")
        else:
            outcome_text = f"failed: {outcome.code}"
            # The agent's messages are whole sentences ("No internet connection to Claude."):
            # drop their full stop, so the line never reads "Claude.. You can try again."
            reason = (outcome.message or outcome.code).rstrip(".")
            reason += "" if reason.endswith(("?", "!")) else "."
            await self.emit(
                EventDraft(
                    task_id=tid,
                    type="error",
                    announce=Announce(
                        text=f"The task failed. {reason} You can try again.",
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
        if self.conversation:
            await self._follow_up()

    async def cancel(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task or not task.active or not task.job:
            return False
        task.job.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task.job), timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        if task.job.cancelled() and task.active:
            # Canceled before its first step: _run never ran, so nothing said so and the task
            # would stay queued, refusing every later request.
            await self._state(task_id, "canceled", "Canceled.", "high")
            self.history.append(TurnSummary(request=task.request.text, outcome="canceled"))
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
        if not text.strip():
            # A quick tap sends stop twice (the client's stop barrier, so a late start can't
            # leave the mic open), and each stop yields an empty transcript. Treat the pair as
            # one tap, so nothing reacts twice (e.g. the approval said again twice).
            now = time.monotonic()
            if now - self._last_empty < EMPTY_PAIR_S:
                return
            self._last_empty = now
        if self.conversation:
            try:
                await self._converse(text, client_request_id)
            except Exception:  # spoken, never silent, and the session keeps working
                log.exception("conversation failed")
                active = self.active_task
                await self.error(
                    "conversation_failed",
                    "Sorry, that didn't work.",
                    "Please say it again.",
                    task_id=active.task_id if active else None,
                )
            return
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

    # ----- conversation (§5.4, only with ARTEMIS_CONVERSATION=on) -----

    async def _converse(self, text: str, client_request_id: str) -> None:
        """Ask the controller what this utterance is, then do exactly that."""
        decision = self.conversation.decide(text)
        await self.emit(
            EventDraft(
                type="transcript.final",
                announce=Announce(text=f"Heard: {text}", priority="normal") if text else None,
                data=TranscriptData(
                    text=text,  # always the raw transcript
                    client_request_id=client_request_id,
                    intent=decision.intent if text.strip() else None,
                ).model_dump(),
            )
        )
        active = self.active_task
        tid = active.task_id if active else None
        action = decision.action
        if action == "none":
            return
        if action == "submit":
            await self.submit(TaskRequest(client_request_id=client_request_id, source="voice", text=decision.text))
            return
        if action == "cancel" and active:
            await self.cancel(active.task_id)
        elif action == "repeat":
            await self.voice.repeat_last()
        elif action == "stop_speech":
            was_speaking = self._speaking()
            await self.voice.stop_speech()
            if decision.say and not was_speaking:
                # Nothing was being said, so "stop" may have meant the task: never stay silent.
                await self.say(tid, decision.say)
        elif action == "rate":
            set_rate = getattr(self.voice, "set_rate", None)  # optional (SAPI only, Yaovi)
            if set_rate is None:
                await self.say(tid, decision.say)
            else:
                result = set_rate(decision.delta)
                if inspect.isawaitable(result):
                    result = await result
                await self._say_rate(tid, decision, result)
        elif decision.say:  # "say", and "queue" (the controller already kept the request)
            await self.say(tid, decision.say, decision.priority)
        if decision.reminder:
            await self.say(tid, decision.reminder)

    async def _say_rate(self, tid: str | None, decision, result) -> None:
        """Say the outcome of a rate change through the bus, so it reaches the log and the live region."""
        if isinstance(result, bool):  # an older voice: True = changed, False = did not say why
            if result:
                await self.say(tid, RATE_LINES["faster" if decision.delta > 0 else "slower"])
            elif not self._voice_ready():
                await self.say(tid, decision.say)
            return
        status = getattr(result, "value", result)  # a RateStatus (str Enum) or its plain string
        line = decision.say if status == "no_output" else RATE_LINES.get(status)
        if line:
            await self.say(tid, line)

    def _voice_ready(self) -> bool:
        """voice_ready as voice.status reports it; False if the voice can't tell us."""
        announcer = getattr(self.voice, "announcer", None)
        try:
            return bool(announcer.status()[0])
        except Exception:
            return False

    def _speaking(self) -> bool:
        """True only when we know Artemis is speaking. NVDA can't tell us, so that is False."""
        announcer = getattr(self.voice, "announcer", None)
        try:
            return bool(announcer.current().busy())
        except Exception:
            return False

    async def _follow_up(self) -> None:
        """A task just ended: start the request that was waiting, or say it was dropped."""
        follow = self.conversation.next_request()
        if follow is None:
            return
        await self.say(None, follow.say)
        if follow.start:
            await self.submit(TaskRequest(client_request_id=new_id("cr"), source="voice", text=follow.start))

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
                announce=Announce(text=spoken_error(message, recovery), priority="high"),
                data=ErrorData(code=code, message=message, recovery=recovery).model_dump(),
            )
        )

    async def voice_status(self, voice_ready: bool, screen_reader: str | None) -> None:
        await self.emit(
            EventDraft(type="voice.status", data={"voice_ready": voice_ready, "screen_reader": screen_reader})
        )
