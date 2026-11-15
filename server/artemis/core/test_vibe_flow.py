"""The core vibe-coding flow, end to end over HTTP (contracts.md §9, §10).

"Create a Python program that prints Hello World, open it in VS Code, and run it": the core is the
real app with ClaudeAgent on a scripted model (artemis.test_no_secret_leaks's Launch and
ScriptedModel). Plan, create_project, write_files, run_program, open_path, then a short answer.
The developer approves each dialog (Enter), as the test_no_secret_leaks sweep does. Only the
desktop is replaced: find_app resolves "code" to a fake desktop id, and _spawn_detached (the
gtk-launch that would open the window) is recorded instead of run.
"""

import os
import sys
import uuid

import pytest

# The desktop tools are offered and accepted only on Linux (contracts.md §9).
pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="desktop tools are Linux-only"
)

pytest.importorskip("anthropic")

from artemis.policy import desktop as desktop_module
from artemis.test_no_secret_leaks import FINAL, Launch, answer, tool

FAKE_CODE_ID = "fake-vscode-test.desktop"
FAKE_CODE_NAME = "Visual Studio Code"
CODE_NAMES = {"code", "vs code", "vscode", "visual studio code"}
HELLO = 'print("Hello, World!")\n'
TEXT = "Create a Python program that prints Hello World, open it in VS Code, and run it"
SPOKEN_MAX = 120  # a spoken line is one short sentence


@pytest.fixture
def launch(tmp_path, monkeypatch):
    if not os.environ.get("ARTEMIS_RUN_PYTHON"):
        from artemis.test_no_secret_leaks import _separate_python

        if python := _separate_python():
            monkeypatch.setenv("ARTEMIS_RUN_PYTHON", python)
    return Launch(tmp_path, monkeypatch)


def fake_desktop(monkeypatch):
    """ "code" is a known app with a fake desktop id, and launching it only records the argv."""
    real_find_app = desktop_module.find_app
    opened: list[list[str]] = []

    def find_app(query: str):
        if query.strip().lower() in CODE_NAMES:
            return FAKE_CODE_ID, FAKE_CODE_NAME
        return real_find_app(query)

    async def spawn(argv: list[str]) -> None:
        opened.append(argv)

    monkeypatch.setattr(desktop_module, "find_app", find_app)
    monkeypatch.setattr(desktop_module, "_spawn_detached", spawn)
    return opened


def plan(goal: str) -> tuple[int, dict]:
    """The plan names the steps the ring acts out: write -> editor, run -> terminal, open -> the app."""
    return tool(
        "plan",
        goal=goal,
        steps=[
            {"say": "I'll make a project and write main.py.", "action": "think"},
            {
                "say": "I'll write main.py in the editor.",
                "action": "write",
                "target": "editor",
            },
            {
                "say": "I'll run main.py in the terminal.",
                "action": "run",
                "target": "terminal",
            },
            {
                "say": "I'll open main.py in VS Code.",
                "action": "open",
                "target": FAKE_CODE_NAME,
            },
        ],
    )


# Friction found by this test (see the report): open_path refuses main.py with path_rejected ("is a
# program or a script, so opening it would run it") before it looks at with_app, so VS Code is never
# asked. Strict xfail: the suite stays green, and the marker must come off once open_path accepts a
# .py file when the app that opens it does not run files.
@pytest.mark.xfail(
    strict=True,
    reason="open_path refuses main.py (.py is a runnable suffix) even with_app=code",
)
async def test_hello_world_open_in_vscode_and_run(launch, monkeypatch):
    opened = fake_desktop(monkeypatch)
    async with launch as run:
        sid = await run.session()
        # Every step is scripted, the plan first: nothing here relies on planned() inserting one.
        run.model.steps.extend(
            [
                plan(TEXT),
                tool("create_project", name="hello world"),
                tool("write_files", files=[{"path": "main.py", "content": HELLO}]),
                tool("run_program", entry="main.py", args=[]),
                tool("open_path", path="main.py", with_app="code"),
                answer(
                    "I opened main.py in VS Code and ran it. It printed Hello, World!"
                ),
            ]
        )
        body = {
            "client_request_id": f"cr_{uuid.uuid4().hex[:12]}",
            "source": "keyboard",
            "text": TEXT,
        }
        tid = (await run.call("POST", f"/sessions/{sid}/tasks", json=body)).json()[
            "task_id"
        ]
        await run.wait(lambda: run.state(tid) in FINAL, 60)

        # The task succeeds, and every scripted turn was used.
        assert run.state(tid) == "succeeded", run.said(tid)
        assert not run.model.steps

        # plan_required never fired: the plan came first, so no tool was refused for want of one.
        assert not [e for e in run.events if e["type"] == "error"], [
            e["data"] for e in run.events if e["type"] == "error"
        ]
        assert not any("plan_required" in body for _url, _h, body in run.model.requests)

        # Approvals, counted by kind. The write and the run each need one (the developer said Enter).
        # The open needs one today (kind "open"). Once another agent's change lands and open stops
        # asking, this count drops to 0: the test accepts either, and fails only on another kind.
        # TODO(open-no-approval): when opening main.py in an editor needs no approval, assert open == 0.
        kinds = [
            e["data"]["kind"] for e in run.events if e["type"] == "approval.requested"
        ]
        counts = {kind: kinds.count(kind) for kind in set(kinds)}
        assert counts.get("write_files") == 1, counts
        assert counts.get("run_program") == 1, counts
        assert counts.get("open", 0) in (0, 1), counts
        assert set(kinds) <= {"write_files", "run_program", "open"}, counts
        assert run.decisions and all(status in (200, 204) for status in run.decisions)

        # Spoken lines: short, and in the order the work happened.
        said = run.said(tid)
        assert said, "nothing was spoken"
        assert all(len(line) <= SPOKEN_MAX for line in said), said
        wrote = next(
            i for i, line in enumerate(said) if line.startswith("Created main.py")
        )
        ran = next(
            i
            for i, line in enumerate(said)
            if line.startswith("main.py ran successfully")
        )
        opened_at = next(
            i for i, line in enumerate(said) if line.startswith("Opened main.py")
        )
        assert wrote < ran < opened_at, said
        assert any("Hello, World!" in line for line in said), said

        # The ring: write -> editor or file, run -> terminal, open -> the app, one done at the end.
        cues = [
            e["data"]
            for e in run.events
            if e["type"] == "ring.cue" and e["task_id"] == tid
        ]
        by_action = {c["action"]: c for c in cues}
        assert by_action["write"]["target"] in ("editor", "file", "main.py"), cues
        assert by_action["run"]["target"] in ("terminal", "main.py"), cues
        assert by_action["open"]["target"] == FAKE_CODE_NAME, cues
        assert [c["action"] for c in cues].count("done") == 1, cues
        assert cues[-1]["action"] == "done", cues
        assert (
            [c["action"] for c in cues].index("write")
            < [c["action"] for c in cues].index("run")
            < [c["action"] for c in cues].index("open")
        ), cues

        # The run really printed it, and the open went to the fake app's launcher with the file.
        (result,) = [r for slug, entry, r in run.runs if entry == "main.py"]
        assert "Hello, World!" in result.stdout_tail
        assert opened == [
            ["gtk-launch", FAKE_CODE_ID, str(run.projects / "hello-world" / "main.py")]
        ], opened
