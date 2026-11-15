"""Which tools the model is offered (contracts.md sections 9 and 10)."""

import pytest

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
    assert not set(offered) & LINUX_ONLY_TOOLS
    assert "plan" in offered and {"read_path", "type_text", "press_keys"} <= set(offered)
    # The order of the rest is the same as on Linux, so the cache key only differs by what is missing.
    assert offered == [n for n in names("linux") if n not in LINUX_ONLY_TOOLS]
