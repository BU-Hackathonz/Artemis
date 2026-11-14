"""The controller inside a real Session (contracts.md §5.4): stub agent, fake runner, fake voice."""

import asyncio

import pytest

from artemis.agent.stub import StubAgent
from artemis.contracts import Answered, Style, TaskRequest
from artemis.conversation import Controller
from artemis.conversation.controller import KEYBOARD_ONLY, WAITING
from artemis.core.session import Session
from artemis.policy.testing import FakeRunner


class FakeSpeaker:
    def __init__(self):
        self.is_busy = False

    def busy(self):
        return self.is_busy


class FakeAnnouncer:
    def __init__(self):
        self.speaker = FakeSpeaker()

    def current(self):
        return self.speaker


class FakeVoice:
    def __init__(self):
        self.spoken: list[str] = []
        self.calls: list[str] = []
        self.announcer = FakeAnnouncer()

    async def announce(self, text, priority):
        self.spoken.append(text)

    async def stop_speech(self):
        self.calls.append("stop_speech")

    async def repeat_last(self):
        self.calls.append("repeat")


class World:
    def __init__(self, tmp_path, agent=None, conversation=True):
        self.voice = FakeVoice()
        projects = tmp_path / "projects"
        self.session = Session(
            "ses_1",
            voice=self.voice,
            runner=FakeRunner(projects),
            agent=agent or StubAgent(),
            projects_dir=projects,
            heartbeat_s=60,  # no "still working" lines in these tests
            conversation=conversation,
        )
        self._n = 0

    async def say(self, text):
        """The developer said something (push-to-talk released)."""
        self._n += 1
        await self.session.spoken(text, f"cr_{self._n}")

    async def until(self, predicate, timeout=5.0):
        async def poll():
            while not predicate():
                await asyncio.sleep(0.005)

        await asyncio.wait_for(poll(), timeout)

    def pending(self):
        approvals = self.session.approvals.pending()
        return approvals[0] if approvals else None

    async def approve(self):
        await self.until(lambda: self.pending() is not None)
        approval = self.pending()
        self.session.approvals.resolve(approval.approval_id, "approve", approval.approval_hash)
        await self.until(lambda: self.pending() is None or self.pending().approval_id != approval.approval_id)

    def transcripts(self):
        return [(e.data["text"], e.data["intent"]) for e in self.session.bus.history if e.type == "transcript.final"]

    def tasks(self):
        return list(self.session.tasks.values())


async def at_first_approval(tmp_path):
    w = World(tmp_path)
    await w.say("Create a Python program that prints Hello World")
    await w.until(lambda: w.pending() is not None)
    return w


# ----- the flag -----


def test_the_controller_is_off_unless_the_flag_says_on(tmp_path, monkeypatch):
    def make(**kwargs):
        return Session("s", voice=FakeVoice(), runner=FakeRunner(tmp_path), agent=StubAgent(), projects_dir=tmp_path, **kwargs)

    monkeypatch.delenv("ARTEMIS_CONVERSATION", raising=False)
    assert make().conversation is None
    monkeypatch.setenv("ARTEMIS_CONVERSATION", "off")
    assert make().conversation is None
    monkeypatch.setenv("ARTEMIS_CONVERSATION", "on")
    assert isinstance(make().conversation, Controller)
    assert make(conversation=False).conversation is None


async def test_with_the_flag_off_stop_still_cancels_as_before(tmp_path):
    w = World(tmp_path, conversation=False)
    await w.say("Create a Python program that prints Hello World")
    await w.until(lambda: w.pending() is not None)
    await w.say("Stop.")
    await w.until(lambda: not w.session.active_task)
    assert w.tasks()[0].state == "canceled"
    assert w.transcripts()[-1] == ("Stop.", None)


# ----- one conversation, flag on -----


async def test_a_spoken_request_starts_a_task_with_the_raw_words(tmp_path):
    w = await at_first_approval(tmp_path)
    assert w.transcripts() == [("Create a Python program that prints Hello World", "request")]
    assert w.tasks()[0].request.text == "Create a Python program that prints Hello World"


async def test_a_status_question_is_answered_without_a_new_task_or_touching_the_approval(tmp_path):
    w = await at_first_approval(tmp_path)
    approval = w.pending()
    await w.say("What are you doing?")
    assert w.voice.spoken[-1] == f"I'm waiting for your decision. {approval.summary}"
    assert len(w.tasks()) == 1 and w.pending().approval_id == approval.approval_id
    assert w.transcripts()[-1] == ("What are you doing?", "status")


@pytest.mark.parametrize("said", ["Yes.", "Approve it", "no"])
async def test_spoken_words_never_decide_an_approval(tmp_path, said):
    w = await at_first_approval(tmp_path)
    await w.say(said)
    assert w.voice.spoken[-1] == KEYBOARD_ONLY
    assert w.pending() is not None and w.tasks()[0].active
    assert not list((tmp_path / "projects").glob("**/*.py"))  # nothing was written


async def test_stop_talking_stops_the_voice_and_leaves_the_task_and_the_approval(tmp_path):
    w = await at_first_approval(tmp_path)
    await w.say("Stop talking.")
    assert w.voice.calls == ["stop_speech"]
    assert w.tasks()[0].state == "awaiting_approval" and w.pending() is not None
    assert w.voice.spoken[-1] == WAITING  # the open decision is never hidden by an interruption
    await w.say("repeat that")
    assert w.voice.spoken[-1] == w.pending().summary and w.voice.calls == ["stop_speech"]


class Slow:
    """An agent that waits until the test lets it finish, crash or be canceled."""

    def __init__(self, crash=False):
        self.release = asyncio.Event()
        self.crash = crash
        self.seen: list = []

    async def run(self, task, tools, emit):
        self.seen.append(task)
        if len(self.seen) == 1:
            await self.release.wait()
            if self.crash:
                raise RuntimeError("boom")
        return Answered(text="Done.")


async def test_stop_with_nothing_being_said_tells_them_how_to_cancel(tmp_path):
    w = World(tmp_path, agent=Slow())
    await w.say("build a to-do app")
    await w.until(lambda: w.tasks()[0].state == "working")
    await w.say("stop")
    assert w.voice.spoken[-1] == "Say cancel to stop the task." and w.tasks()[0].active
    # When speech really was cut off, that was the point: nothing more is said.
    w.voice.announcer.speaker.is_busy = True
    await w.say("stop")
    assert w.voice.spoken[-1] == "Heard: stop" and w.voice.calls == ["stop_speech", "stop_speech"]
    w.session.agent.release.set()


async def test_a_request_during_a_task_waits_and_then_starts_read_back(tmp_path):
    agent = Slow()
    w = World(tmp_path, agent=agent)
    await w.say("build a to-do app")
    await w.until(lambda: w.tasks()[0].state == "working")
    await w.say("Then add a dark mode toggle.")
    assert w.voice.spoken[-1] == "I'll do that next." and len(w.tasks()) == 1
    await w.say("and a footer")
    assert w.voice.spoken[-1] == "I already have one waiting: Then add a dark mode toggle. Say cancel to drop it."

    agent.release.set()
    await w.until(lambda: len(w.tasks()) == 2 and not w.session.active_task)
    first, second = w.tasks()
    assert first.state == "succeeded" and second.state == "succeeded"
    assert second.request.text == "Then add a dark mode toggle."  # the exact words, not a rewrite
    spoken = w.voice.spoken
    start = spoken.index("Starting the follow-up.")
    assert spoken[start + 1] == "Working on: Then add a dark mode toggle."
    assert [t.request for t in agent.seen] == ["build a to-do app", "Then add a dark mode toggle."]


async def test_cancel_drops_the_waiting_request_first_and_the_task_second(tmp_path):
    agent = Slow()
    w = World(tmp_path, agent=agent)
    await w.say("build a to-do app")
    await w.until(lambda: w.tasks()[0].state == "working")
    await w.say("then add tests")
    await w.say("Never mind.")
    assert w.voice.spoken[-1] == "Dropped: then add tests. The current task is still going. Say cancel again to stop it."
    assert w.tasks()[0].active
    await w.say("cancel")
    await w.until(lambda: not w.session.active_task)
    assert w.tasks()[0].state == "canceled" and len(w.tasks()) == 1


async def test_a_waiting_request_is_dropped_out_loud_when_the_task_fails(tmp_path):
    agent = Slow(crash=True)
    w = World(tmp_path, agent=agent)
    await w.say("build a to-do app")
    await w.until(lambda: w.tasks()[0].state == "working")
    await w.say("then add tests")
    agent.release.set()
    await w.until(lambda: any(line.startswith("Dropped the follow-up") for line in w.voice.spoken))
    assert w.tasks()[0].state == "failed" and len(w.tasks()) == 1 and len(agent.seen) == 1


async def test_keep_it_brief_reaches_the_agent_with_the_next_task(tmp_path):
    agent = Slow()
    agent.release.set()
    w = World(tmp_path, agent=agent)
    await w.say("Keep it brief.")
    assert w.voice.spoken[-1] == "Okay, I'll keep it brief." and w.tasks() == []
    await w.say("visual mode")
    await w.say("build a to-do app")
    await w.until(lambda: w.tasks() and not w.session.active_task)
    assert agent.seen[0].style == Style(verbosity="brief", focus="visual")


async def test_a_typed_request_is_tracked_too(tmp_path):
    w = World(tmp_path)
    await w.session.submit(TaskRequest(client_request_id="cr_k", source="keyboard", text="make a hello program"))
    await w.until(lambda: w.pending() is not None)
    await w.say("status")
    assert w.voice.spoken[-1].startswith("I'm waiting for your decision.")


async def test_slower_explains_itself_until_the_voice_can_change_rate(tmp_path):
    w = World(tmp_path)
    await w.say("slower")
    assert w.voice.spoken[-1] == "I can't change how fast I speak yet."
    rates = []
    w.voice.set_rate = rates.append
    await w.say("faster")
    assert rates == [1]
