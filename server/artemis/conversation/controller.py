"""The conversation controller: one per session.

It watches the session's events (`observe`) so it always knows the task, its state, the open
approval and the files changed, and it turns each utterance into a `Decision` (`decide`).
The core carries the decision out. Nothing here talks to a model, a microphone or the disk,
and nothing here can approve anything: approvals stay on the keyboard.
"""

from dataclasses import dataclass, field
from typing import Literal

from artemis.contracts import Style

from .intents import match, simplify

# The one NVDA sentence: the controller uses it, and the core speaks it when a rate change reports NVDA.
NVDA_RATE_LINE = "I'm speaking through NVDA, so its own speech rate setting controls how fast I talk."

Action = Literal[
    "none",  # nothing to do (an empty transcript or only the wake word, no approval open)
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
NOTHING_TO_APPROVE = "There's nothing to approve right now."
# The core's heartbeat line (core/session.py STILL_WORKING). It is not a step of the task, so
# status never reports it.
HEARTBEAT = "Still working."
_MAX_SPOKEN_REQUEST = 80


@dataclass(frozen=True)
class Decision:
    intent: str  # an intent name from intents.py, "request", "empty" or "wake_word"
    action: Action = "none"
    say: str | None = None
    priority: Literal["low", "normal", "high"] = "normal"
    text: str | None = None  # the developer's words, unchanged, for submit and queue
    delta: int = 0
    # Spoken after the action when an approval is open, so an interruption never hides it.
    reminder: str | None = None


@dataclass(frozen=True)
class FollowUp:
    say: str
    start: str | None = None  # the request to start now; None when it was dropped


@dataclass
class _Task:
    task_id: str
    request: str
    state: str = "queued"
    progress: str = ""  # the latest progress line
    answer: str = ""
    files: list[str] = field(default_factory=list)
    run: str = ""  # the last program run, as observed ("exited with code 0", "timed out")
    scan: str = ""  # the last security scan result, as observed

    @property
    def active(self) -> bool:
        return self.state not in FINAL_STATES

    def verified(self) -> list[str]:
        """Sentences about checks that actually reported, in the order they happened to be learned."""
        facts = []
        if self.run:
            facts.append(f"The last run {self.run}.")
        if self.scan:
            facts.append(f"The security scan {self.scan}.")
        return facts


def _short(text: str) -> str:
    text = " ".join(text.split()).rstrip(".")
    return text if len(text) <= _MAX_SPOKEN_REQUEST else text[:_MAX_SPOKEN_REQUEST].rstrip() + "…"


def _end(text: str) -> str:
    """The text as the end of a sentence: a question or exclamation keeps its mark, anything
    else gets one full stop, so "add a docstring?" is never spoken as "add a docstring?."."""
    return text if text.endswith(("?", "!")) else text + "."


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
        elif event.type == "agent.message":
            if data.get("kind") == "progress":
                if data.get("text", "") != HEARTBEAT:
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
        elif event.type == "run.result":
            if data.get("timed_out"):
                task.run = "timed out"
            elif isinstance(data.get("exit_code"), int):
                task.run = f"exited with code {data['exit_code']}"
        elif event.type == "security.findings":
            findings = data.get("findings")
            if isinstance(findings, list):
                high = sum(1 for f in findings if isinstance(f, dict) and f.get("severity") == "high")
                if not findings:
                    task.scan = "reported no findings"
                else:
                    count = "1 finding" if len(findings) == 1 else f"{len(findings)} findings"
                    task.scan = f"reported {count}" + (f", {high} high severity" if high else "")

    def next_request(self) -> "FollowUp | None":
        """Call when a task has ended: what to do with the request that was waiting, if any.

        After a success it starts, and is read back first so a misheard one never starts
        unnoticed. After a failure it is dropped and said so, because it may have depended on
        the task that failed. A canceled task already took it along (see `observe`).
        """
        text, self.queued = self.queued, None
        if text is None:
            return None
        if self.task is not None and self.task.state == "succeeded":
            # The core then announces "Working on: <request>" as for any task: that is the read-back.
            return FollowUp("Starting the follow-up.", start=text)
        return FollowUp(f"Dropped the follow-up, because the task before it didn't finish: {_end(_short(text))}")

    # ----- what the developer said -----

    def decide(self, transcript: str) -> Decision:
        if not transcript.strip() or not simplify(transcript):
            # An accidental push-to-talk tap, or only the wake word: never a task. The tap (or
            # the wake word) may have cut the approval announcement off, so say it again, once.
            intent = "empty" if not transcript.strip() else "wake_word"
            if self.approval:
                return Decision(intent, "say", self.approval, "high")
            return Decision(intent)
        intent = match(transcript)
        task = self.task
        active = task is not None and task.active
        reminder = WAITING if self.approval else None

        if intent == "cancel":
            if self.queued is not None:
                # The follow-up goes first: it hasn't started, so dropping it loses nothing.
                said = f"Dropped: {_end(_short(self.queued))}"
                self.queued = None
                if active:
                    said += " The current task is still going. Say cancel again to stop it."
                return Decision(intent, "say", said, reminder=reminder)
            if active:
                return Decision(intent, "cancel")
            return Decision(intent, "say", "Nothing to cancel.")
        if intent == "stop_speaking":
            # If nothing was being said, "stop" probably meant the task: never answer with silence.
            hint = None if reminder or not active else "Say cancel to stop the task."
            return Decision(intent, "stop_speech", say=hint, reminder=reminder)
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
            self.style = Style(verbosity=intent, focus=self.style.focus)
            said = {
                "brief": "Okay, I'll keep it brief.",
                "detailed": "Okay, I'll explain in detail.",
                "normal": "Okay, back to normal answers.",
            }[intent]
            return Decision(intent, "say", said, reminder=reminder)
        if intent in ("visual", "code"):
            self.style = Style(verbosity=self.style.verbosity, focus=intent)
            said = (
                "Visual mode. I'll describe the layout and how accessible it is."
                if intent == "visual"
                else "Code mode. I'll explain the files and functions I change."
            )
            return Decision(intent, "say", said, reminder=reminder)
        if intent in ("slower", "faster"):
            if self.screen_reader == "nvda":
                said = NVDA_RATE_LINE
                return Decision(intent, "say", said, reminder=reminder)
            return Decision(
                intent, "rate", "I can't change how fast I speak yet.",
                delta=-1 if intent == "slower" else 1, reminder=reminder,
            )
        if intent == "approval_word":
            if self.approval or (active and task.state == "awaiting_approval"):
                # A misheard "yes" must never write or run anything.
                return Decision(intent, "say", KEYBOARD_ONLY, "high")
            if not active and self.expecting_reply:
                # The agent's last answer was a question, so "yes" or "no" is the answer to it
                # (design A4: the next request is the reply). Safe: anything it leads to that
                # writes or runs still needs a keyboard approval.
                return Decision("request", "submit", text=transcript)
            # Otherwise never a task or a follow-up: there is nothing for "yes" to answer.
            return Decision(intent, "say", NOTHING_TO_APPROVE)

        # Anything else is for the agent, in the developer's own words.
        if active:
            if self.queued is not None:
                # One slot, and it is never replaced silently.
                said = f"I already have one waiting: {_end(_short(self.queued))} Say cancel to drop it."
                return Decision("request", "say", said, reminder=reminder)
            self.queued = transcript
            said = "I'll do that next."
            if self.approval:
                said += " A decision is still waiting: Enter to approve, Escape to reject."
            return Decision("request", "queue", said, text=transcript)
        return Decision("request", "submit", text=transcript)

    # ----- fixed sentences -----

    def status(self) -> str:
        task = self.task
        if task is None:
            return "I'm not working on anything yet."
        about = _end(_short(task.request))
        if task.state == "awaiting_approval" and self.approval:
            said = f"I'm waiting for your decision. {self.approval}"
        elif task.state == "running":
            said = f"I'm running the program for: {about}"
        elif task.active:
            said = f"I'm working on: {about}"
            if task.progress:
                said += f" Last step: {task.progress.rstrip('.')}."
            if task.files:
                said += f" So far I changed {_names(task.files)}."
        elif task.state == "succeeded":
            said = f"I finished: {about}"
            if task.files:
                said += f" I changed {_names(task.files)}."
        elif task.state == "canceled":
            said = f"The last request was canceled: {about}"
        else:
            said = f"The last request failed: {about}"
        # Only what was actually observed: a run exit code and a scan result. Nothing is said about
        # a check that never reported, and nothing here calls a failure a success.
        if not (task.state == "awaiting_approval" and self.approval):  # the decision comes first, alone
            said += "".join(f" {fact}" for fact in task.verified())
        if self.queued:
            said += f" Next up: {_end(_short(self.queued))}"
        return said

    def changed_files(self) -> str:
        task = self.task
        if task is None or not task.files:
            return "I haven't changed any files yet." if task is None or task.active else "I didn't change any files in the last request."
        count = "1 file" if len(task.files) == 1 else f"{len(task.files)} files"
        when = "so far" if task.active else "in the last request"
        return f"I changed {count} {when}: {_names(task.files)}."
