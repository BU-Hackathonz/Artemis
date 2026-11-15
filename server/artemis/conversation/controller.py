"""The conversation controller: one per session.

It watches the session's events (`observe`) so it always knows the task, its state, the open
approval and the files changed, and it turns each utterance into a `Decision` (`decide`).
The core carries the decision out. Nothing here talks to a model, a microphone or the disk,
and nothing here can approve anything: approvals stay on the keyboard.
"""

import logging
import time
from collections.abc import Callable
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
    "decide",  # answer the open approval by voice: `verdict` for `approval_id` with `approval_hash` (§5.4)
]

# Voice approval (contracts.md §5.4, Proposed (David)): only these words decide, never "yes" or "no".
VOICE_APPROVE = frozenset({"approve", "approve it"})
VOICE_REJECT = frozenset({"reject", "reject it"})
# Approving by voice is for the project only: a scanned write or a Python run. Rejecting works for
# any approval. Anything blocked by the scan, every desktop kind and every command stays on the keyboard.
VOICE_APPROVABLE = frozenset({"write_files", "run_program"})
VOICE_REVEAL_S = 1.5  # like the dialog's reveal guard: never a decision on words said before it was heard

log = logging.getLogger(__name__)

FINAL_STATES = ("succeeded", "failed", "canceled")
KEYBOARD_ONLY = "Use the keyboard to decide: Enter on the dialog to approve, Escape to reject."
VOICE_WORDS = "Say approve or reject, or use the keyboard: Enter to approve, Escape to reject."
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
    rearm_heard: bool = False  # the approval's summary is said again: the core waits for it to be heard
    verdict: Literal["approve", "reject"] | None = None  # "decide" only
    approval_id: str | None = None
    approval_hash: str | None = None
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
        # While a program is running, "the last run" would sound like it is about this one.
        if self.run and self.state != "running":
            facts.append(f"The last run {self.run}.")
        if self.scan:
            facts.append(f"The security scan {self.scan}.")
        return facts


def _short(text: str) -> str:
    text = " ".join(text.split()).rstrip(".")
    return text if len(text) <= _MAX_SPOKEN_REQUEST else text[:_MAX_SPOKEN_REQUEST].rstrip() + "…"


def _scan_text(findings: list) -> str:
    """What the scan reported, from the findings it sent. scan_project sends at most 50, so a full
    list is said as "50 or more"."""
    if not findings:
        return "reported no findings"
    high = sum(1 for f in findings if isinstance(f, dict) and f.get("severity") == "high")
    count = "1 finding" if len(findings) == 1 else f"{len(findings)} findings"
    if len(findings) >= 50:
        count = "50 or more findings"
    return f"reported {count}" + (f", {high} high severity" if high else "")


def _end(text: str) -> str:
    """The text as the end of a sentence: a question or exclamation keeps its mark, anything
    else ends in one full stop, so "add a docstring?" is never "add a docstring?." and
    "add a docstring," or "rename it to:" never end in ",." or ":." (Avery's review on #108)."""
    text = text.rstrip()
    if text.endswith(("?", "!")):
        return text
    return text.rstrip(".,:;…").rstrip() + "."


def _names(files: list[str]) -> str:
    if len(files) <= 3:
        return files[0] if len(files) == 1 else ", ".join(files[:-1]) + " and " + files[-1]
    return ", ".join(files[:3]) + f" and {len(files) - 3} more"


class Controller:
    def __init__(self, style: Style | None = None, on_style: Callable[[Style], object] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        """`style` is where to start (the saved one, if the core remembers it); `on_style` is told
        whenever the developer changes it, so the core can save it. A failing `on_style` is
        ignored: the style still applies for this session."""
        self.style = style or Style()
        self._on_style = on_style
        self.queued: str | None = None  # one follow-up, started when the active task ends
        self.task: _Task | None = None  # the active task, or the last one
        self.approval: str | None = None  # the open approval's spoken summary
        self.open_approval: dict | None = None  # its id, hash, kind, blocking and when it opened (voice approval)
        self._clock = clock
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
                self.open_approval = None
                self.expecting_reply = task.state == "succeeded" and task.answer.rstrip().endswith("?")
        elif event.type == "agent.message":
            if data.get("kind") == "progress":
                if data.get("text", "") != HEARTBEAT:
                    task.progress = data.get("text", "")
            else:
                task.answer = data.get("text", "")
        elif event.type == "approval.requested":
            self.approval = data.get("summary") or "An approval is open."
            self.open_approval = {
                "id": data.get("approval_id"), "hash": data.get("approval_hash"), "kind": data.get("kind"),
                # "at" is when its summary was heard: set by the core once Artemis falls silent after
                # saying it (approval_heard), never while it is still queued or was cut off.
                "blocking": bool(data.get("blocking")), "at": None,
            }
            # The approval carries the findings that apply to what it proposes. A clean one replaces
            # an earlier finding, which is no longer true of the code about to be written or run.
            if isinstance(data.get("findings"), list):
                task.scan = _scan_text(data["findings"]) if data["findings"] else ""
        elif event.type == "approval.resolved":
            self.approval = None
            self.open_approval = None
        elif event.type == "file.changed":
            path = data.get("path")
            if path and path not in task.files:
                task.files.append(path)
        elif event.type == "run.result":
            if data.get("timed_out"):
                task.run = "timed out"
            elif isinstance(data.get("exit_code"), int):
                code = data["exit_code"]
                # Worded like the run announcement: a non-zero exit is a failure.
                task.run = "exited with code 0" if code == 0 else f"failed with exit code {code}"
        elif event.type == "security.findings":
            findings = data.get("findings")
            if isinstance(findings, list):
                task.scan = _scan_text(findings)

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

    def approval_heard(self, approval_id: str) -> None:
        """The core heard Artemis finish the summary of this approval: the reveal guard starts now."""
        a = getattr(self, "open_approval", None)
        if a is not None and a.get("id") == approval_id and a.get("at") is None:
            a["at"] = self._clock()

    def _voice_decision(self, words: str) -> Decision:
        """An approval word while an approval is open. "approve" decides a clean project write or run,
        "reject" decides any approval, and "yes" or "no" never decide anything: a misheard "yes" must
        never write or run."""
        a = getattr(self, "open_approval", None)
        if a is None or not a.get("id") or not a.get("hash"):
            return Decision("approval_word", "say", KEYBOARD_ONLY, "high")  # an approval voice can't name
        if words not in VOICE_APPROVE | VOICE_REJECT:
            return Decision("approval_word", "say", VOICE_WORDS, "high")  # "yes" and "no" never decide
        if words in VOICE_REJECT:  # rejecting is always safe, heard or not
            return Decision("approval_word", "decide", "Rejected.", "high", verdict="reject",
                            approval_id=a["id"], approval_hash=a["hash"])
        if a["kind"] not in VOICE_APPROVABLE or a["blocking"]:
            why = "the security scan flagged it" if a["blocking"] else "it reaches outside the project"
            return Decision("approval_word", "say",
                            f"That one needs the keyboard, because {why}. Press Enter on the dialog to approve, or say reject.", "high")
        if a["at"] is None:
            # Not heard yet: still queued behind other speech, or push-to-talk cut it off. Say it now.
            summary = self.approval or "An approval is open."
            return Decision("approval_word", "say", f"Let me read it first. {summary} Then say approve or reject.",
                            "high", rearm_heard=True)
        if self._clock() - a["at"] < VOICE_REVEAL_S:
            return Decision("approval_word", "say", "Let me finish reading it first. Then say approve or reject.", "high")
        return Decision("approval_word", "decide", "Approved.", "high", verdict="approve",
                        approval_id=a["id"], approval_hash=a["hash"])

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
        if intent == "reset_style":
            self._set_style(Style())
            said = "Okay, I've forgotten your answer style. Back to normal answers."
            return Decision(intent, "say", said, reminder=reminder)
        if intent in ("brief", "detailed", "normal"):
            self._set_style(Style(verbosity=intent, focus=self.style.focus))
            said = {
                "brief": "Okay, I'll keep it brief.",
                "detailed": "Okay, I'll explain in detail.",
                "normal": "Okay, back to normal answers.",
            }[intent]
            return Decision(intent, "say", said, reminder=reminder)
        if intent in ("visual", "code"):
            self._set_style(Style(verbosity=self.style.verbosity, focus=intent))
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
                return self._voice_decision(simplify(transcript))
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

    def _set_style(self, style: Style) -> None:
        self.style = style
        if self._on_style is not None:
            try:
                self._on_style(style)
            except Exception:  # saving must never break a conversation
                log.exception("could not tell the core about the new answer style")

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
                said += f" Last step: {_end(task.progress)}"
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
        decision_open = task.state == "awaiting_approval" and bool(self.approval)
        if self.queued and not decision_open:  # an open decision is said alone, nothing after it
            said += f" Next up: {_end(_short(self.queued))}"
        return said

    def changed_files(self) -> str:
        task = self.task
        if task is None or not task.files:
            return "I haven't changed any files yet." if task is None or task.active else "I didn't change any files in the last request."
        count = "1 file" if len(task.files) == 1 else f"{len(task.files)} files"
        when = "so far" if task.active else "in the last request"
        return f"I changed {count} {when}: {_names(task.files)}."
