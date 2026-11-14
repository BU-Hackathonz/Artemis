"""The conversation controller: one per session.

It watches the session's events (`observe`) so it always knows the task, its state, the open
approval and the files changed, and it turns each utterance into a `Decision` (`decide`).
The core carries the decision out. Nothing here talks to a model, a microphone or the disk,
and nothing here can approve anything: approvals stay on the keyboard.
"""

from dataclasses import dataclass, field
from typing import Literal

from .intents import match

Verbosity = Literal["brief", "normal", "detailed"]
Focus = Literal["none", "visual", "code"]
Action = Literal[
    "none",  # nothing to do (an empty transcript)
    "submit",  # start a task with `text`
    "queue",  # a task is running: `text` was kept and becomes the next task
    "say",  # just speak `say`
    "stop_speech",
    "repeat",  # repeat the last announcement (the voice remembers it)
    "cancel",  # cancel the active task
    "rate",  # change the speaking rate by `delta` steps; speak `say` if the voice can't
]

FINAL_STATES = ("succeeded", "failed", "canceled")
KEYBOARD_ONLY = "Use the keyboard to decide: Enter on the dialog to approve, Escape to reject."
WAITING = "A decision is still waiting. Say repeat to hear it again."
_MAX_SPOKEN_REQUEST = 80


@dataclass(frozen=True)
class Style:
    """How the developer asked to be answered. The agent gets it with each task."""

    verbosity: Verbosity = "normal"
    focus: Focus = "none"


@dataclass(frozen=True)
class Decision:
    intent: str  # an intent name from intents.py, or "request"
    action: Action = "none"
    say: str | None = None
    priority: Literal["low", "normal", "high"] = "normal"
    text: str | None = None  # the developer's words, unchanged, for submit and queue
    delta: int = 0
    # Spoken after the action when an approval is open, so an interruption never hides it.
    reminder: str | None = None


@dataclass
class _Task:
    task_id: str
    request: str
    state: str = "queued"
    progress: str = ""  # the latest progress line
    answer: str = ""
    files: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.state not in FINAL_STATES


def _short(text: str) -> str:
    text = " ".join(text.split()).rstrip(".")
    return text if len(text) <= _MAX_SPOKEN_REQUEST else text[:_MAX_SPOKEN_REQUEST].rstrip() + "…"


def _names(files: list[str]) -> str:
    if len(files) <= 3:
        return files[0] if len(files) == 1 else ", ".join(files[:-1]) + " and " + files[-1]
    return ", ".join(files[:3]) + f" and {len(files) - 3} more"


class Controller:
    def __init__(self) -> None:
        self.style = Style()
        self.queued: str | None = None  # one follow-up, started when the active task ends
        self.task: _Task | None = None  # the active task, or the last one
        self.approval: str | None = None  # the open approval's spoken summary
        self.screen_reader: str | None = None
        self.expecting_reply = False  # the last answer was a question

    # ----- what the session tells us -----

    def begin(self, task_id: str, request: str) -> None:
        """A task was started (spoken or typed)."""
        self.task = _Task(task_id, request)
        self.approval = None
        self.expecting_reply = False

    def observe(self, event) -> None:
        """Every event the session emits, in order. Unknown ones are ignored."""
        data = event.data or {}
        if event.type == "voice.status":
            self.screen_reader = data.get("screen_reader")
            return
        task = self.task
        if task is None or event.task_id != task.task_id:
            return
        if event.type == "task.state":
            task.state = data.get("state", task.state)
            if not task.active:
                self.approval = None
                self.expecting_reply = task.state == "succeeded" and task.answer.rstrip().endswith("?")
                if task.state == "canceled":
                    self.queued = None  # "cancel" means everything they asked for
        elif event.type == "agent.message":
            if data.get("kind") == "progress":
                task.progress = data.get("text", "")
            else:
                task.answer = data.get("text", "")
        elif event.type == "approval.requested":
            self.approval = data.get("summary") or "An approval is open."
        elif event.type == "approval.resolved":
            self.approval = None
        elif event.type == "file.changed":
            path = data.get("path")
            if path and path not in task.files:
                task.files.append(path)

    def next_request(self) -> tuple[str, str] | None:
        """Call when a task has ended. Returns (request, announcement) for the follow-up to
        start now, or None. The announcement reads the request back, so a misheard follow-up
        never starts unnoticed."""
        text, self.queued = self.queued, None
        if text is None:
            return None
        return text, f"Now the follow-up: {_short(text)}."

    # ----- what the developer said -----

    def decide(self, transcript: str) -> Decision:
        if not transcript.strip():
            return Decision("empty")
        intent = match(transcript)
        task = self.task
        active = task is not None and task.active
        reminder = WAITING if self.approval else None

        if intent == "cancel":
            if self.queued is not None:
                # The follow-up goes first: it hasn't started, so dropping it loses nothing.
                self.queued = None
                said = "Okay, I dropped the follow-up."
                if active:
                    said += " The current task is still going. Say cancel again to stop it."
                return Decision(intent, "say", said, reminder=reminder)
            if active:
                return Decision(intent, "cancel")
            return Decision(intent, "say", "Nothing to cancel.")
        if intent == "stop_speaking":
            return Decision(intent, "stop_speech", reminder=reminder)
        if intent == "repeat":
            if self.approval:
                # The decision matters more than whatever was said last.
                return Decision(intent, "say", self.approval, "high")
            return Decision(intent, "repeat")
        if intent == "status":
            return Decision(intent, "say", self.status())
        if intent == "changed_files":
            return Decision(intent, "say", self.changed_files(), reminder=reminder)
        if intent in ("brief", "detailed", "normal"):
            self.style = Style(intent, self.style.focus)
            said = {
                "brief": "Okay, I'll keep it brief.",
                "detailed": "Okay, I'll explain in detail.",
                "normal": "Okay, back to normal answers.",
            }[intent]
            return Decision(intent, "say", said, reminder=reminder)
        if intent in ("visual", "code"):
            self.style = Style(self.style.verbosity, intent)
            said = (
                "Visual mode. I'll describe the layout and how accessible it is."
                if intent == "visual"
                else "Code mode. I'll explain the files and functions I change."
            )
            return Decision(intent, "say", said, reminder=reminder)
        if intent in ("slower", "faster"):
            if self.screen_reader == "nvda":
                said = "I'm speaking through NVDA, so its own speech rate setting controls how fast I talk."
                return Decision(intent, "say", said, reminder=reminder)
            return Decision(
                intent, "rate", "I can't change how fast I speak yet.",
                delta=-1 if intent == "slower" else 1, reminder=reminder,
            )
        if intent == "approval_word" and active and task.state == "awaiting_approval":
            # A misheard "yes" must never write or run anything.
            return Decision(intent, "say", KEYBOARD_ONLY, "high")

        # Anything else is for the agent, in the developer's own words.
        if active:
            replaced = self.queued is not None
            self.queued = transcript
            said = "I'll do that next, instead of the earlier follow-up." if replaced else "I'll do that next."
            if self.approval:
                said += " A decision is still waiting: Enter to approve, Escape to reject."
            return Decision("request", "queue", said, text=transcript)
        return Decision("request", "submit", text=transcript)

    # ----- fixed sentences -----

    def status(self) -> str:
        task = self.task
        if task is None:
            return "I'm not working on anything yet."
        about = _short(task.request)
        if task.state == "awaiting_approval" and self.approval:
            said = f"I'm waiting for your decision. {self.approval}"
        elif task.state == "running":
            said = f"I'm running the program for: {about}."
        elif task.active:
            said = f"I'm working on: {about}."
            if task.progress:
                said += f" Last step: {task.progress.rstrip('.')}."
        elif task.state == "succeeded":
            said = f"I finished: {about}."
            if task.files:
                said += f" I changed {_names(task.files)}."
        elif task.state == "canceled":
            said = f"The last request was canceled: {about}."
        else:
            said = f"The last request failed: {about}."
        if self.queued:
            said += f" Next up: {_short(self.queued)}."
        return said

    def changed_files(self) -> str:
        task = self.task
        if task is None or not task.files:
            return "I haven't changed any files yet." if task is None or task.active else "I didn't change any files in the last request."
        count = "1 file" if len(task.files) == 1 else f"{len(task.files)} files"
        when = "so far" if task.active else "in the last request"
        return f"I changed {count} {when}: {_names(task.files)}."
