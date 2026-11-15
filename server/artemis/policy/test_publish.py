"""publish_github (contracts.md §9, Proposed): the project goes to GitHub only after a keyboard
approval that names the repository, its visibility and every file; secrets never leave."""

import asyncio
import os
import sys

import pytest

from artemis.contracts import Decision
from artemis.policy.gateway import ToolGateway
from artemis.policy.testing import EventLog, FakeApprovals, FakeRunner

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="the desktop tools exist only on Linux (contracts.md §9)")

FAKE_GH = """#!/bin/sh
echo "$@" >> "$GH_LOG"
if [ "$1" = api ]; then [ -n "$GH_SIGNED_OUT" ] && exit 1; echo "octo-dev 42"; exit 0; fi
if [ "$1" = repo ] && [ "$2" = create ]; then
  # what would be pushed: the files and the history of the folder gh was started in
  echo "FILES $(git ls-files | tr '\\n' ' ')" >> "$GH_LOG"
  echo "COMMITS $(git rev-list --count HEAD) AUTHOR $(git log -1 --format=%ae)" >> "$GH_LOG"
  echo "REMOTES $(git remote | tr '\\n' ' ')" >> "$GH_LOG"
  exit 0
fi
exit 0
"""


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    # No git identity on purpose: a fresh demo laptop has none, and publishing must still work.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    log = tmp_path / "gh.log"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_LOG", str(log))  # where the fake gh writes its arguments
    projects = home / "ArtemisProjects"
    project = projects / "tip-calculator"
    project.mkdir(parents=True)
    (project / "index.html").write_text("<h1>Tip calculator</h1>\n")
    (project / "tip.py").write_text("def tip(total, pct):\n    return total * pct / 100\n")
    return projects, project, log


def gate(projects, decisions):
    events, approvals = EventLog(), FakeApprovals(decisions)
    g = ToolGateway(FakeRunner(projects), approvals, events, projects_dir=projects)
    return g, events, approvals


def publish(g, **args):
    async def go():
        await g.call("t1", "open_project", {"name": "tip-calculator"})
        return await g.call("t1", "publish_github", {"repo": "tip-calculator", "public": False, "description": None, **args})
    return asyncio.run(go())


@pytest.fixture(autouse=True)
def gh_log_reaches_the_fake(monkeypatch):
    # The fake gh writes its arguments to GH_LOG; let that one variable through the env allowlist.
    from artemis.policy import desktop
    real = desktop._publish_env
    monkeypatch.setattr(desktop, "_publish_env", lambda: {**real(), "GH_LOG": os.environ["GH_LOG"],
                                                          **({"GH_SIGNED_OUT": "1"} if os.environ.get("GH_SIGNED_OUT") else {})})


def test_an_approved_private_publish_commits_and_creates_the_repo(world):
    projects, project, log = world
    g, events, approvals = gate(projects, [Decision.approve])
    result = publish(g)
    assert result.ok, result.message
    assert result.data["repository"] == "octo-dev/tip-calculator" and result.data["visibility"] == "private"
    [asked] = approvals.requests
    assert asked.kind == "publish" and asked.blocking is False
    assert "private repository, octo-dev/tip-calculator" in asked.summary and "index.html" in asked.summary
    calls = log.read_text()
    assert "repo create octo-dev/tip-calculator --private --source . --remote origin --push" in calls
    assert not (project / ".git").exists()  # the project folder is never touched: the push comes from a private copy


def test_public_needs_the_override_key(world):
    projects, _, log = world
    g, _, approvals = gate(projects, [Decision.reject])
    result = publish(g, public=True)
    assert approvals.requests[0].blocking is True and result.error == "scan_blocked"
    assert "repo create" not in log.read_text()


def test_a_rejected_publish_pushes_nothing(world):
    projects, project, log = world
    g, _, _ = gate(projects, [Decision.reject])
    assert publish(g).error == "rejected_by_user"
    assert "repo create" not in log.read_text() and not (project / ".git").exists()


def test_a_file_with_a_secret_is_never_published(world):
    projects, project, log = world
    (project / "config.py").write_text('API_KEY = "sk-ant-api03-' + "x" * 40 + '"\n')
    g, _, approvals = gate(projects, [Decision.approve])
    assert publish(g).error == "path_rejected" and approvals.requests == []
    assert "repo create" not in (log.read_text() if log.exists() else "")


def test_signed_out_of_github_is_a_clear_error(world, monkeypatch):
    projects, _, _ = world
    monkeypatch.setenv("GH_SIGNED_OUT", "1")
    g, _, approvals = gate(projects, [Decision.approve])
    result = publish(g)
    assert result.error == "tool_failed" and "gh auth login" in result.message and approvals.requests == []


def test_a_file_changed_during_the_approval_is_not_published(world):
    projects, project, log = world

    def change(_approval):
        (project / "tip.py").write_text("print('changed')\n")
        return Decision.approve
    g, _, _ = gate(projects, [change])
    assert publish(g).error == "invalidated"
    assert "repo create" not in log.read_text()


def pushed(log):
    lines = log.read_text().splitlines()
    files = next(line for line in lines if line.startswith("FILES ")).split()[1:]
    commits = next(line for line in lines if line.startswith("COMMITS "))
    return sorted(files), commits


def test_what_is_pushed_is_exactly_the_approved_files_with_one_commit(world):
    projects, _, log = world
    g, _, approvals = gate(projects, [Decision.approve])
    assert publish(g).ok
    files, commits = pushed(log)
    assert files == ["index.html", "tip.py"] == [f.path for f in approvals.requests[0].files]
    assert commits.startswith("COMMITS 1 AUTHOR 42+octo-dev@users.noreply.github.com")


def test_an_existing_git_folder_adds_no_history_files_or_hooks(world, tmp_path):
    projects, project, log = world
    run_git = lambda *a: __import__("subprocess").run(["git", "-c", "user.name=x", "-c", "user.email=x@x", *a],
                                                      cwd=project, check=True, capture_output=True)
    run_git("init", "-q")
    (project / "old-secret.txt").write_text("from an old commit\n")
    run_git("add", "-A")
    run_git("commit", "-q", "-m", "wip")
    (project / "old-secret.txt").unlink()
    marker = tmp_path / "hook-ran"
    hook = project / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    run_git("config", "core.fsmonitor", f"touch {marker}")
    run_git("remote", "add", "origin", "https://example.invalid/other.git")
    g, _, _ = gate(projects, [Decision.approve])
    assert publish(g).ok
    files, commits = pushed(log)
    assert files == ["index.html", "tip.py"] and commits.startswith("COMMITS 1 ")
    assert [line.strip() for line in log.read_text().splitlines() if line.startswith("REMOTES")] == ["REMOTES"]
    assert not marker.exists()


def test_publishing_twice_works(world):
    projects, _, log = world
    g, _, _ = gate(projects, [Decision.approve, Decision.approve])
    assert publish(g).ok
    assert asyncio.run(g.call("t1", "publish_github", {"repo": "tip-calc-v2", "public": False, "description": None})).ok
    assert "repo create octo-dev/tip-calc-v2" in log.read_text()


def test_ignored_files_are_left_out_not_an_error(world):
    projects, project, log = world
    (project / ".gitignore").write_text("*.log\n")
    (project / "debug.log").write_text("noise\n")
    (project / ".pytest_cache").mkdir()
    (project / ".pytest_cache" / ".gitignore").write_text("*\n")
    (project / ".pytest_cache" / "README.md").write_text("cache\n")
    g, _, approvals = gate(projects, [Decision.approve])
    assert publish(g).ok
    files, _ = pushed(log)
    assert files == [".gitignore", "index.html", "tip.py"]
    assert "debug.log" not in approvals.requests[0].summary


@pytest.mark.parametrize("name,content", [("deploy.py", "TOKEN = 'ghp_" + "a" * 36 + "'\n"),
                                          ("db.py", "URL = 'postgres://admin:hunter2@db.example/app'\n"),
                                          ("server.pem", "anything\n"),
                                          ("settings.py", "aws_secret_access_key = abc\n")])
def test_more_kinds_of_secret_stop_the_publish(world, name, content):
    projects, project, _ = world
    (project / name).write_text(content)
    g, _, approvals = gate(projects, [Decision.approve])
    assert publish(g).error == "path_rejected" and approvals.requests == []


def test_the_description_is_shown_and_hashed(world):
    projects, _, _ = world
    g, _, approvals = gate(projects, [Decision.reject])
    publish(g, description="A tip splitter")
    asked = approvals.requests[0]
    assert "A tip splitter" in asked.summary and "A tip splitter" in asked.desktop.text
