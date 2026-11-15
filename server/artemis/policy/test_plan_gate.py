"""The plan gate (contracts.md section 10): re-plans, read-only tools, recovery, cue timing, cleanup."""

import asyncio
import sys

import pytest

from artemis.contracts import Decision
from artemis.policy.gateway import ToolGateway
from artemis.policy.testing import EventLog, FakeApprovals, FakeRunner

PLAN = {"goal": "Make it", "steps": [{"say": "Write.", "action": "write", "target": "editor"}]}
FILES = {"files": [{"path": "a.py", "content": "x = 1\n"}]}


def gate(tmp_path, decisions=None, require_plan=True):
    log, approvals = EventLog(), FakeApprovals(decisions or [])
    projects = tmp_path / "p"
    projects.mkdir(exist_ok=True)
    g = ToolGateway(FakeRunner(projects), approvals, log, projects_dir=projects, require_plan=require_plan)
    return g, log


def run(coro):
    return asyncio.run(coro)


async def _flow(g, steps):
    return [await g.call("t1", name, args) for name, args in steps]


def test_read_only_tools_need_no_plan(tmp_path):
    g, log = gate(tmp_path)
    results = run(_flow(g, [("create_project", {"name": "demo"}), ("list_files", {"dir": None})]))
    assert all(r.ok for r in results)
    cues = [(e.data["step"], e.data["action"], e.data["target"]) for e in log.of("ring.cue")]
    assert cues == [(None, "think", "editor"), (None, "read", "file")]  # no plan: no step, but a real target


def test_acting_tools_cue_where_the_work_is_without_a_plan(tmp_path):
    g, log = gate(tmp_path, require_plan=False)
    run(_flow(g, [("create_project", {"name": "demo"}), ("write_files", FILES), ("list_files", {"dir": None})]))
    cues = [(e.data["step"], e.data["action"], e.data["target"]) for e in log.of("ring.cue")]
    assert cues == [(None, "think", "editor"), (None, "write", "editor"), (None, "read", "file")]


def test_the_plan_is_announced_with_a_think_cue(tmp_path):
    g, log = gate(tmp_path)
    run(g.call("t1", "plan", PLAN))
    assert [e.type for e in log.events] == ["agent.plan", "ring.cue"]
    assert log.of("ring.cue")[0].data == {"step": None, "action": "think", "target": None}


def test_plan_required_says_how_to_recover(tmp_path):
    g, _ = gate(tmp_path)
    run(g.call("t1", "create_project", {"name": "demo"}))
    first, _, after = run(_flow(g, [("write_files", FILES), ("plan", PLAN), ("write_files", FILES)]))
    assert first.error == "plan_required" and "plan" in first.message.lower()
    assert after.ok  # the same call works once the plan is in


def test_a_new_plan_resets_the_cued_steps(tmp_path):
    g, log = gate(tmp_path)
    plan = {"goal": "Look", "steps": [{"say": "Read.", "action": "read", "target": "file"}]}
    run(g.call("t1", "create_project", {"name": "demo"}))
    run(_flow(g, [("plan", plan), ("list_files", {"dir": None})]))
    run(_flow(g, [("list_files", {"dir": None}), ("plan", plan), ("list_files", {"dir": None})]))
    steps = [e.data["step"] for e in log.of("ring.cue") if e.data["action"] == "read"]
    assert steps == [0, None, 0]  # the plan's one read step is used once, until the re-plan


def test_a_rejected_write_is_not_cued(tmp_path):
    g, log = gate(tmp_path, [Decision.reject])
    run(g.call("t1", "create_project", {"name": "demo"}))
    assert run(_flow(g, [("plan", PLAN), ("write_files", FILES)]))[1].error == "rejected_by_user"
    assert [e.data["action"] for e in log.of("ring.cue")] == ["think", "think"]  # create, plan: no write
    run(g.call("t1", "write_files", FILES))
    assert [e.data["step"] for e in log.of("ring.cue") if e.data["action"] == "write"] == [0]


@pytest.mark.skipif(sys.platform != "linux", reason="the desktop tools exist only on Linux (contracts.md §9)")
def test_a_self_cued_tool_is_not_cued_before_its_approval(tmp_path):
    g, log = gate(tmp_path, [Decision.reject])
    plan = {"goal": "Run", "steps": [{"say": "Run it.", "action": "run", "target": "terminal"}]}
    run(g.call("t1", "plan", plan))
    result = run(g.call("t1", "run_command", {"command": "ls", "cwd": None}))
    assert result.error == "rejected_by_user"
    assert [e.data["action"] for e in log.of("ring.cue")] == ["think"]  # only the plan's think, no run


def test_ring_done_frees_the_plan_and_the_cues(tmp_path):
    g, log = gate(tmp_path)
    run(_flow(g, [("create_project", {"name": "demo"}), ("plan", PLAN), ("write_files", FILES)]))
    assert "t1" in g._plans and "t1" in g._cued
    run(g.ring_done("t1"))
    assert g._plans == {} and g._cued == {}
    assert log.of("ring.cue")[-1].data["action"] == "done"
    # The next request has to plan again.
    assert run(g.call("t1", "write_files", FILES)).error == "plan_required"


def test_the_plan_is_spoken_step_by_step(tmp_path):
    g, log = gate(tmp_path)
    plan = {"goal": "Make a calculator", "steps": [
        {"say": "I'll start a new project called calculator.", "action": "think", "target": None},
        {"say": "I'll write the calculator", "action": "write", "target": "editor"},
        {"say": "I'll run it once to check it works.", "action": "run", "target": "terminal"}]}
    run(g.call("t1", "plan", plan))
    announce = log.of("agent.plan")[0].announce
    assert announce.priority == "normal"  # never cuts an approval, never dropped like progress
    assert announce.text == ("I'll start a new project called calculator. I'll write the calculator. "
                             "I'll run it once to check it works.")
    run(g.call("t1", "plan", PLAN))
    assert log.of("agent.plan")[1].announce.text == "New plan. Write."


def test_a_long_plan_says_its_first_steps_and_how_many_follow(tmp_path):
    g, log = gate(tmp_path)
    steps = [{"say": f"Step {i}.", "action": "read", "target": None} for i in range(6)]
    run(g.call("t1", "plan", {"goal": "Look around", "steps": steps}))
    assert log.of("agent.plan")[0].announce.text == "Step 0. Step 1. Step 2. Step 3. Then 2 more steps, in the panel."
