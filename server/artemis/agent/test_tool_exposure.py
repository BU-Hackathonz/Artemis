"""Which tools the model is offered (contracts.md sections 9 and 10)."""

import pytest

from artemis.contracts import DESKTOP_TOOL_ARGS

from artemis.agent.prompts import LINUX_ONLY_TOOLS, tool_definitions

DESKTOP = {"open_app", "open_path", "read_path", "run_command", "type_text", "press_keys"}


def names(platform):
    return [t["name"] for t in tool_definitions(platform)]


def test_linux_offers_the_plan_and_all_six_desktop_tools():
    offered = set(names("linux"))
    assert "plan" in offered and DESKTOP <= offered


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_off_linux_the_launchers_and_the_shell_are_hidden(platform):
    offered = names(platform)
    # All six desktop tools are Linux-only (typing needs the GNOME island too); plan stays.
    hidden = set(DESKTOP_TOOL_ARGS) - {"plan"}
    assert not set(offered) & (LINUX_ONLY_TOOLS | hidden)
    assert "plan" in offered
    # The order of the rest is the same as on Linux, so the cache key only differs by what is missing.
    assert offered == [n for n in names("linux") if n not in hidden]
