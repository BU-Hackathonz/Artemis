"""The desktop tools exist on Linux only: elsewhere they are neither offered nor run (the plan stays)."""

import asyncio

import pytest

from artemis.agent.prompts import tool_definitions
from artemis.contracts import DESKTOP_TOOL_ARGS, TOOL_ARGS
from artemis.policy import desktop as desktop_module
from artemis.policy import rules
from artemis.policy.gateway import ToolGateway
from artemis.policy.testing import EventLog, FakeApprovals, FakeRunner

DESKTOP = set(DESKTOP_TOOL_ARGS)


@pytest.mark.parametrize("platform", ["win32", "darwin", "cygwin"])
def test_the_model_is_not_offered_desktop_tools_off_linux(monkeypatch, platform):
    monkeypatch.setattr("sys.platform", platform)
    names = {t["name"] for t in tool_definitions()}
    assert not DESKTOP & names
    assert "plan" in names and names == set(TOOL_ARGS) - DESKTOP


def test_linux_offers_all_six_and_the_plan(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    names = [t["name"] for t in tool_definitions()]
    assert DESKTOP <= set(names) and "plan" in names and names == list(TOOL_ARGS)


@pytest.mark.parametrize("name", sorted(DESKTOP))
def test_a_desktop_call_is_refused_on_windows_and_never_runs(monkeypatch, tmp_path, name):
    monkeypatch.setattr("sys.platform", "win32")
    spawned = []

    async def boom(*a, **k):
        spawned.append(a)
        raise AssertionError("must not start a process")

    monkeypatch.setattr(desktop_module.asyncio, "create_subprocess_exec", boom)
    gate = ToolGateway(FakeRunner(tmp_path), FakeApprovals([]), EventLog(), projects_dir=tmp_path)
    result = asyncio.run(gate.call("t1", name, {}))
    assert result.ok is False and result.error == "not_found"
    assert not spawned


def test_rule_for_keeps_every_other_tool_off_linux(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    for name in set(TOOL_ARGS) - DESKTOP:
        assert rules.rule_for(name) is rules.RULES[name]
    assert all(rules.rule_for(n) is None for n in DESKTOP)
