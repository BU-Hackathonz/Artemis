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
if [ "$1" = api ]; then [ -n "$GH_SIGNED_OUT" ] && exit 1; echo octo-dev; exit 0; fi
if [ "$1" = repo ] && [ "$2" = create ]; then exit 0; fi
exit 0
"""


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\tname = Test Dev\n\temail = dev@example.invalid\n")
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
    assert (project / ".git").is_dir()


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
