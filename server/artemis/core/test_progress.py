"""Spoken progress: the approval reminder, the question hook and the heartbeat's pace."""

import asyncio

from artemis.agent.stub import StubAgent
from artemis.contracts import Answered, TaskRequest
from artemis.core import session as session_module
from artemis.core.session import Session
from artemis.policy.testing import FakeRunner


class Voice:
    def __init__(self):
        self.spoken: list[tuple[str, str]] = []

    async def announce(self, text, priority):
        self.spoken.append((priority, text))


def make(tmp_path, agent=None, **kw) -> tuple[Session, Voice]:
    voice = Voice()
    projects = tmp_path / "projects"
    projects.mkdir(exist_ok=True)
    s = Session("ses_t", voice=voice, runner=FakeRunner(projects), agent=agent or StubAgent(),
                projects_dir=projects, conversation=False, **kw)
    return s, voice


async def until(predicate, timeout=3.0):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


def reminders(voice):
    return [(p, t) for p, t in voice.spoken if t.startswith("Still waiting for your approval.")]


async def test_an_approval_left_open_is_said_again_once(tmp_path):
    s, voice = make(tmp_path, approval_remind_s=0.05, heartbeat_s=60)
    task = await s.submit(TaskRequest(client_request_id="cr_1", source="keyboard", text="hello world"))
    await until(lambda: s.approvals.pending())
    approval = s.approvals.pending()[0]
    await until(lambda: reminders(voice))
    await asyncio.sleep(0.2)
    assert reminders(voice) == [("high", f"Still waiting for your approval. {approval.summary}")]
    s.approvals.resolve(approval.approval_id, "reject", approval.approval_hash)
    await until(lambda: not task.active)


async def test_a_decided_approval_is_not_reminded(tmp_path):
    s, voice = make(tmp_path, approval_remind_s=0.3, heartbeat_s=60)
    task = await s.submit(TaskRequest(client_request_id="cr_1", source="keyboard", text="hello world"))
    await until(lambda: s.approvals.pending())
    approval = s.approvals.pending()[0]
    s.approvals.resolve(approval.approval_id, "reject", approval.approval_hash)
    await until(lambda: not task.active)
    await asyncio.sleep(0.5)
    assert reminders(voice) == [] and not s._reminders


async def test_a_task_that_ends_with_a_question_calls_the_hook(tmp_path):
    asked = []

    class Asks:
        def __init__(self, text):
            self.text = text

        async def run(self, task, tools, emit):
            return Answered(text=self.text)

    for text, expected in (("What should I call it?", 1), ("Done. It's in the log.", 1)):
        s, _ = make(tmp_path, agent=Asks(text), on_question=lambda: asked.append(1), heartbeat_s=60)
        task = await s.submit(TaskRequest(client_request_id="cr_1", source="keyboard", text="make a file"))
        await until(lambda: not task.active)
        assert len(asked) == expected


async def test_the_heartbeat_slows_down_in_a_long_silence(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "HEARTBEAT_MAX_S", 0.3)
    release = asyncio.Event()

    class Slow:
        async def run(self, task, tools, emit):
            await release.wait()
            return Answered(text="Done.")

    s, voice = make(tmp_path, agent=Slow(), heartbeat_s=0.1)
    task = await s.submit(TaskRequest(client_request_id="cr_1", source="keyboard", text="think hard"))
    await asyncio.sleep(1.05)
    release.set()
    await until(lambda: not task.active)
    beats = [t for _, t in voice.spoken if t == session_module.STILL_WORKING]
    # At 0.1 s every 0.1 s would be about ten; 0.1, then 0.2, then every 0.3 s is four.
    assert 3 <= len(beats) <= 5
