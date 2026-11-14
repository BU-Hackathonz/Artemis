"""edit_file: search/replace edits turned into an approved, hashed diff (decisions.md §9)."""

import asyncio

import pytest

from artemis.contracts import Decision, Finding
from artemis.policy.gateway import ToolGateway
from artemis.policy.testing import EventLog, FakeApprovals, FakeRunner

LOGIN = "def greet(user):\n    print('Hello')\n    print('Hello')\n    return user\n"


class World:
    def __init__(self, tmp_path, script=None, scan=None, content=LOGIN):
        self.projects = tmp_path / "ArtemisProjects"
        self.root = self.projects / "old-app"
        self.root.mkdir(parents=True)
        (self.root / "app.py").write_bytes(content.encode("utf-8"))
        self.log = EventLog()
        self.approvals = FakeApprovals(script)
        self.gateway = ToolGateway(
            FakeRunner(self.projects), self.approvals, self.log, projects_dir=self.projects, scan=scan
        )

    async def edit(self, *edits, path="app.py", opened=True):
        if opened and self.gateway.project is None:
            await self.gateway.call("tsk_1", "open_project", {"name": "old-app"})
        payload = {"path": path, "edits": [{"old": old, "new": new} for old, new in edits]}
        return await self.gateway.call("tsk_1", "edit_file", payload)

    def text(self):
        return (self.root / "app.py").read_bytes().decode("utf-8")


def test_edit_in_an_opened_project_is_one_approved_diff(tmp_path):
    w = World(tmp_path)
    result = asyncio.run(w.edit(("    return user\n", "    return user.strip()\n")))
    assert result.ok
    assert w.text() == LOGIN.replace("return user", "return user.strip()")
    approval = w.approvals.requests[0]
    assert approval.kind == "write_files"
    assert approval.summary == (
        "Change 1 file in old-app: modify app.py, 1 line added, 1 removed. "
        "Enter to approve, Escape to reject."
    )
    assert approval.files[0].model_dump() == {
        "path": "app.py", "action": "modified", "lines_added": 1, "lines_removed": 1,
    }
    assert "-    return user\n+    return user.strip()\n" in approval.diff
    assert w.log.types() == [
        "agent.message", "task.state", "approval.requested", "approval.resolved", "file.changed", "task.state",
    ]
    assert w.log.spoken()[-1] == "Changed app.py, 1 line."


def test_several_edits_apply_in_order(tmp_path):
    w = World(tmp_path)
    result = asyncio.run(w.edit(("def greet(user):", "def greet(name):"), ("return user", "return name")))
    assert result.ok and "def greet(name):" in w.text() and "return name" in w.text()
    assert len(w.approvals.requests) == 1


def test_rejected_edit_changes_nothing(tmp_path):
    w = World(tmp_path, script=[Decision.reject])
    result = asyncio.run(w.edit(("return user", "return None")))
    assert result.error == "rejected_by_user" and w.text() == LOGIN
    assert "file.changed" not in w.log.types()


def test_old_text_must_be_found(tmp_path):
    w = World(tmp_path)
    result = asyncio.run(w.edit(("return usr", "return None")))
    assert result.error == "invalid_args" and "Edit 1" in result.message and "not found" in result.message
    assert w.approvals.requests == [] and w.text() == LOGIN


def test_old_text_must_match_exactly_once(tmp_path):
    w = World(tmp_path)
    result = asyncio.run(w.edit(("def greet(user):", "def greet(u):"), ("    print('Hello')\n", "")))
    assert result.error == "invalid_args" and "Edit 2" in result.message and "2 times" in result.message
    assert w.approvals.requests == [] and w.text() == LOGIN  # the first edit wasn't applied either


@pytest.mark.parametrize("edits", [[("return user", "return user")], [("", "x")]])
def test_edits_that_change_nothing_or_match_nothing(tmp_path, edits):
    w = World(tmp_path)
    assert asyncio.run(w.edit(*edits)).error == "invalid_args"
    assert w.approvals.requests == []


def test_no_edits_is_invalid(tmp_path):
    w = World(tmp_path)
    assert asyncio.run(w.edit()).error == "invalid_args"


def test_missing_file_and_no_project(tmp_path):
    w = World(tmp_path)
    assert asyncio.run(w.edit(("a", "b"), opened=False)).error == "not_found"
    assert asyncio.run(w.edit(("a", "b"), path="nope.py")).error == "not_found"


@pytest.mark.parametrize("path", ["../outside.py", ".git/config", ".env", "C:\\Windows\\win.ini"])
def test_paths_are_checked(tmp_path, path):
    w = World(tmp_path)
    assert asyncio.run(w.edit(("a", "b"), path=path)).error == "path_rejected"
    assert w.approvals.requests == []


def test_only_writable_file_types_can_be_edited(tmp_path):
    w = World(tmp_path)
    (w.root / "run.bat").write_text("echo hi\n")
    assert asyncio.run(w.edit(("echo hi", "del /q *"), path="run.bat")).error == "path_rejected"
    assert (w.root / "run.bat").read_text() == "echo hi\n"


def test_binary_file_is_rejected(tmp_path):
    w = World(tmp_path)
    (w.root / "data.txt").write_bytes(b"\xff\xfe\x00binary")
    assert asyncio.run(w.edit(("binary", "text"), path="data.txt")).error == "path_rejected"


def test_windows_line_endings_are_kept(tmp_path):
    w = World(tmp_path, content=LOGIN.replace("\n", "\r\n"))
    # The model writes \n; the file uses \r\n.
    result = asyncio.run(w.edit(("    print('Hello')\n    return user\n", "    return user\n")))
    assert result.ok
    assert w.text() == "def greet(user):\r\n    print('Hello')\r\n    return user\r\n"


def test_file_changed_during_approval_is_invalidated_and_asked_again(tmp_path):
    w = World(tmp_path)

    def someone_edits(approval):
        (w.root / "app.py").write_text(LOGIN + "# added elsewhere\n", newline="\n")
        return Decision.approve

    w.approvals.script = [someone_edits]
    result = asyncio.run(w.edit(("return user", "return None")))
    assert result.ok
    assert w.text() == LOGIN.replace("return user", "return None") + "# added elsewhere\n"
    assert len(w.approvals.requests) == 2
    assert [e.data["by"] for e in w.log.of("approval.resolved")] == ["invalidated", "user"]


def test_edit_that_no_longer_applies_after_approval_writes_nothing(tmp_path):
    w = World(tmp_path)

    def someone_rewrites(approval):
        (w.root / "app.py").write_text("print('all new')\n", newline="\n")
        return Decision.approve

    w.approvals.script = [someone_rewrites]
    result = asyncio.run(w.edit(("return user", "return None")))
    assert result.error == "invalid_args" and w.text() == "print('all new')\n"
    assert "file.changed" not in w.log.types()


def test_edit_is_scanned_before_approval(tmp_path):
    seen = []

    async def scan(folder):
        seen.append((folder / "app.py").read_text())
        return [Finding(rule="B602", severity="high", path="app.py", line=2, message="shell=True")]

    w = World(tmp_path, script=[Decision.reject], scan=scan)
    result = asyncio.run(w.edit(("    print('Hello')\n    return", "    run(shell=True)\n    return")))
    assert "run(shell=True)" in seen[0]  # the scan saw the file as it would be after the edit
    assert w.approvals.requests[0].blocking is True
    assert result.error == "scan_blocked" and w.text() == LOGIN


def test_cancel_during_approval_writes_nothing(tmp_path):
    async def go():
        waiting = asyncio.Event()

        async def never(approval):
            waiting.set()
            await asyncio.Event().wait()

        w = World(tmp_path, script=[never])
        running = asyncio.create_task(w.edit(("return user", "return None")))
        await waiting.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        return w

    w = asyncio.run(go())
    assert w.text() == LOGIN and w.log.spoken()[-1] == "Canceled. Nothing was written."
