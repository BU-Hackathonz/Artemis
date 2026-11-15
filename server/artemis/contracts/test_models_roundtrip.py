"""Every event data model and the desktop and plan models round-trip, so the gate and the events can't drift."""

import pytest

from artemis.contracts import DATA_MODELS, DESKTOP_TOOL_ARGS, PLAN_ARGS, TOOL_ARGS, PlanArgs, ReadPathArgs
from artemis.contracts.events import PlanStepData

SAMPLES = {
    "agent.plan": {"goal": "Say hello", "steps": [{"say": "I'll write it.", "action": "write", "target": "editor"}]},
    "ring.cue": {"step": 0, "action": "write", "target": "editor"},
    "desktop.action": {"op_id": "op_1", "kind": "type", "text": "hi", "window": "code.desktop: main.py"},
}


@pytest.mark.parametrize("kind", sorted(SAMPLES))
def test_new_event_data_round_trips(kind):
    model = DATA_MODELS[kind]
    assert model.model_validate(model.model_validate(SAMPLES[kind]).model_dump()).model_dump() == model.model_validate(SAMPLES[kind]).model_dump()


def test_a_plan_step_is_the_same_on_both_sides():
    args = PlanArgs.model_validate(SAMPLES["agent.plan"])
    assert [PlanStepData.model_validate(s.model_dump()) for s in args.steps]
    assert set(PlanStepData.model_fields) == set(type(args.steps[0]).model_fields)


def test_plan_is_not_a_desktop_tool():
    assert "plan" in PLAN_ARGS and "plan" not in DESKTOP_TOOL_ARGS


def test_read_path_lines_must_be_in_order():
    with pytest.raises(ValueError):
        ReadPathArgs(path="~/a.txt", start_line=5, end_line=2)


@pytest.mark.parametrize("keys,ok", [("ctrl+s", True), ("Return", True), ("ctrl+shift+grave", True),
                                     ("ctrl+s; rm", False), ("super", True), ("a b", False)])
def test_press_keys_syntax(keys, ok):
    from pydantic import ValidationError

    from artemis.contracts import PressKeysArgs
    if ok:
        PressKeysArgs(keys=keys)
    else:
        with pytest.raises(ValidationError):
            PressKeysArgs(keys=keys)
