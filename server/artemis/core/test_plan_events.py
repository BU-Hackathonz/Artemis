"""agent.plan and ring.cue from the core (contracts.md section 10)."""

import asyncio

import pytest

from artemis.contracts import Answered, Canceled, EventDraft, Failed, TaskRequest
from artemis.core.session import Session

def make(tmp_path, agent=None):
    return Session("ses_t", voice=None, runner=None, agent=agent, projects_dir=tmp_path)


def of(session, type_):
    return [e for e in session.bus.history if e.type == type_]


class Ends:
    """An agent that ends with the given outcome."""

    def __init__(self, outcome=None, wait=False):
        self.outcome, self.wait = outcome or Answered(text="ok"), wait

    async def run(self, task, tools, emit):
        if self.wait:
            await asyncio.sleep(60)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


async def run_task(session, cid="c1"):
    task = await session.submit(TaskRequest(client_request_id=cid, source="keyboard", text="hi"))
    if task.job:
        try:
            await task.job
        except asyncio.CancelledError:
            pass
    return task


@pytest.mark.parametrize(
    "outcome, state",
    [(Answered(text="ok"), "succeeded"), (Failed(code="boom", message="x"), "failed"), (Canceled(), "canceled")],
)
async def test_done_cue_once_after_the_final_state(tmp_path, outcome, state):
    s = make(tmp_path, Ends(outcome))
    task = await run_task(s)
    types = [e.type for e in s.bus.history if e.task_id == task.task_id]
    cues = of(s, "ring.cue")
    assert len(cues) == 1 and cues[0].data["action"] == "done" and cues[0].task_id == task.task_id
    final = [e for e in of(s, "task.state") if e.data["state"] == state]
    assert len(final) == 1 and final[0].seq < cues[0].seq
    assert types[-1] == "ring.cue" or "ring.cue" in types[types.index("task.state"):]
    # A repeated final state does not cue again.
    await s.emit(EventDraft(task_id=task.task_id, type="task.state", data={"state": state}))
    assert len(of(s, "ring.cue")) == 1


async def test_no_done_cue_before_the_final_state(tmp_path):
    s = make(tmp_path, Ends(wait=True))
    task = await s.submit(TaskRequest(client_request_id="c1", source="keyboard", text="hi"))
    await asyncio.sleep(0.05)
    assert of(s, "ring.cue") == []
    await s.cancel(task.task_id)
    assert [e.data["action"] for e in of(s, "ring.cue")] == ["done"]


async def test_each_task_gets_its_own_done_cue(tmp_path):
    s = make(tmp_path, Ends())
    t1 = await run_task(s, "c1")
    t2 = await run_task(s, "c2")
    assert [e.task_id for e in of(s, "ring.cue")] == [t1.task_id, t2.task_id]


async def test_cancel_before_start_cues_done_once(tmp_path):
    s = make(tmp_path, Ends(wait=True))
    task = await s.submit(TaskRequest(client_request_id="c1", source="keyboard", text="hi"))
    await s.cancel(task.task_id)
    assert len(of(s, "ring.cue")) == 1
