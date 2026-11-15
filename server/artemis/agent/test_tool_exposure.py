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


def _properties(node):
    """Every property schema in a tool's input schema, including the nested models in $defs."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                yield from value.values()
            yield from _properties(value)
    elif isinstance(node, list):
        for item in node:
            yield from _properties(item)


def test_every_argument_the_model_sees_has_a_description():
    # The model only sees the schema, so each argument (and each field of a nested model) must say
    # what it means. The limits and the file rules are in these descriptions because the schema
    # keeps no minLength, maxLength or minItems.
    from artemis.agent.prompts import tool_definitions

    undescribed = []
    for definition in tool_definitions("linux"):
        for prop in _properties(definition["input_schema"]):
            if not prop.get("description"):
                undescribed.append((definition["name"], prop))
    assert undescribed == []


def test_limits_the_gateway_enforces_are_stated_for_the_model():
    from artemis.agent.prompts import tool_definitions

    schemas = {t["name"]: t["input_schema"] for t in tool_definitions("linux")}
    write = schemas["write_files"]["properties"]["files"]
    assert "20" in write["description"]
    plan = schemas["plan"]["$defs"]["PlanStep"]["properties"]["say"]
    assert "200" in plan["description"]
    assert "12" in schemas["plan"]["properties"]["steps"]["description"]
