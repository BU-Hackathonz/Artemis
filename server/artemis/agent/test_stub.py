"""StubAgent through the real gateway, against fake approvals and a fake runner."""

import asyncio

import pytest

from artemis.agent.config import Config
from artemis.agent.stub import StubAgent
from artemis.contracts import Answered, Decision, Failed, RunResult, TaskContext
from artemis.policy.gateway import ToolGateway
from artemis.policy.testing import EventLog, FakeApprovals, FakeRunner

TASK = TaskContext(task_id="tsk_1", request="create a Python program that prints Hello World")


def run_stub(tmp_path, script=None, result=None, task=TASK):
    log, approvals, runner = EventLog(), FakeApprovals(script), FakeRunner(tmp_path, result)
    gateway = ToolGateway(runner, approvals, log, projects_dir=tmp_path)
    outcome = asyncio.run(StubAgent().run(task, gateway, log))
    return outcome, log, approvals, runner


def test_happy_path_event_order(tmp_path):
    outcome, log, approvals, runner = run_stub(tmp_path)
    assert outcome == Answered(text="I created hello-world with main.py and ran it. It printed Hello, World!")
    assert (tmp_path / "hello-world" / "main.py").read_text() == 'print("Hello, World!")\n'
    assert log.types() == [
        "agent.plan",          # write main.py, then run it (contracts.md §10)
        "ring.cue",            # create_project: think
        "agent.message",       # Starting project hello-world.
        "task.state",          # awaiting_approval
        "approval.requested",  # write_files
        "approval.resolved",
        "ring.cue",            # write_files: the plan's write step, once the developer approved
        "file.changed",
        "task.state",          # working
        "task.state",          # awaiting_approval
        "approval.requested",  # run_program
        "approval.resolved",
        "ring.cue",            # run_program: the plan's run step, once approved
        "task.state",          # running
        "run.result",
        "task.state",          # working
        "agent.message",       # the answer
    ]
    assert all(e.task_id == "tsk_1" for e in log.events)
    assert [a.kind for a in approvals.requests] == ["write_files", "run_program"]
    cues = [e.data for e in log.of("ring.cue")]
    assert [(c["step"], c["action"], c["target"]) for c in cues] == [(None, "think", None), (0, "write", "editor"), (1, "run", "terminal")]
    assert log.spoken() == [
        "Starting project hello-world.",
        "Create project hello-world with 1 file: main.py, 1 line. Not scanned: the security scanner isn't installed. Enter to approve, Escape to reject.",
        "Created main.py, 1 line.",
        "Run main.py with Python. Not scanned: the security scanner isn't installed. Enter to run, Escape to reject.",
        "Running main.py.",
        "main.py ran successfully. It printed: Hello, World!",
        "I created hello-world with main.py and ran it. It printed Hello, World!",
    ]
    answer = log.events[-1]
    assert answer.data == {"text": outcome.text, "kind": "answer"}


def test_rejected_write_creates_nothing(tmp_path):
    outcome, log, approvals, runner = run_stub(tmp_path, script=[Decision.reject])
    assert outcome == Answered(text="Okay, I didn't create anything.")
    assert list(tmp_path.iterdir()) == []
    assert "file.changed" not in log.types() and "run.result" not in log.types()
    assert log.types()[-3:] == ["approval.resolved", "task.state", "agent.message"]
    assert len(approvals.requests) == 1 and runner.runs == []


def test_rejected_run_keeps_the_file_but_does_not_run(tmp_path):
    outcome, log, approvals, runner = run_stub(tmp_path, script=[Decision.approve, Decision.reject])
    assert outcome == Answered(text="Okay, I created hello-world with main.py but didn't run it.")
    assert (tmp_path / "hello-world" / "main.py").exists()
    assert runner.runs == [] and "run.result" not in log.types()


def test_failed_run_is_reported_not_hidden(tmp_path):
    failed = RunResult(
        op_id="", preset="python", exit_code=1, timed_out=False, duration_ms=9,
        stdout_tail="", stderr_tail="NameError: name 'pritn' is not defined", truncated=False,
    )
    outcome, log, _, _ = run_stub(tmp_path, result=failed)
    assert "failed with exit code 1" in outcome.text
    spoken = log.spoken()
    assert spoken.index("main.py failed with exit code 1. NameError: name 'pritn' is not defined.") < len(spoken) - 1


def test_second_task_in_a_session_gets_a_new_project(tmp_path):
    async def go():
        log, approvals, runner = EventLog(), FakeApprovals(), FakeRunner(tmp_path)
        gateway = ToolGateway(runner, approvals, log, projects_dir=tmp_path)
        await StubAgent().run(TASK, gateway, log)
        return await StubAgent().run(TASK.model_copy(update={"task_id": "tsk_2"}), gateway, log)

    assert "hello-world-2" in asyncio.run(go()).text
    assert (tmp_path / "hello-world-2" / "main.py").exists()


def test_cancel_during_approval_leaves_nothing(tmp_path):
    async def go():
        waiting = asyncio.Event()

        async def never(approval):
            waiting.set()
            await asyncio.Event().wait()

        log, approvals, runner = EventLog(), FakeApprovals([never]), FakeRunner(tmp_path)
        gateway = ToolGateway(runner, approvals, log, projects_dir=tmp_path)
        task = asyncio.create_task(StubAgent().run(TASK, gateway, log))
        await waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return log

    log = asyncio.run(go())
    assert list(tmp_path.iterdir()) == []
    assert log.spoken()[-1] == "Canceled. Nothing was written."


def test_other_failures_become_failed(tmp_path):
    (tmp_path / "blocked").mkdir()
    log, approvals, runner = EventLog(), FakeApprovals(), FakeRunner(tmp_path)

    def boom(*args):
        raise OSError("disk full")

    runner.write_file = boom
    gateway = ToolGateway(runner, approvals, log, projects_dir=tmp_path)
    outcome = asyncio.run(StubAgent().run(TASK, gateway, log))
    assert isinstance(outcome, Failed) and outcome.code == "tool_failed"


def test_config_defaults_and_env(monkeypatch, tmp_path):
    for name in ("ARTEMIS_AGENT", "ARTEMIS_MODEL", "ARTEMIS_EFFORT", "ARTEMIS_PROJECTS_DIR", "ARTEMIS_MAX_ITERATIONS"):
        monkeypatch.delenv(name, raising=False)
    default = Config.from_env()
    assert (default.agent, default.model, default.effort, default.max_iterations) == (
        "stub", "claude-opus-5-5", "medium", 12,
    )
    assert default.projects_dir.name == "ArtemisProjects"
    assert not hasattr(default, "api_key")

    monkeypatch.setenv("ARTEMIS_AGENT", "claude")
    monkeypatch.setenv("ARTEMIS_PROJECTS_DIR", str(tmp_path))
    monkeypatch.setenv("ARTEMIS_MAX_ITERATIONS", "5")
    changed = Config.from_env()
    assert (changed.agent, changed.projects_dir, changed.max_iterations) == ("claude", tmp_path, 5)

    monkeypatch.setenv("ARTEMIS_AGENT", "gpt")
    with pytest.raises(ValueError):
        Config.from_env()
