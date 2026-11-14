"""Every sample payload from docs/contracts.md §4 parses against the models."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from artemis.contracts import (
    Approval,
    ApprovalResponse,
    Event,
    EventDraft,
    RunResult,
    TaskRequest,
    WriteFilesArgs,
)

SAMPLES = Path(__file__).parent / "samples"


def load(name: str):
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def test_typed_request():
    assert TaskRequest.model_validate(load("1_typed_request.json")).source == "keyboard"


def test_write_approval():
    sample = load("2_write_approval.json")
    approval = Approval.model_validate(Event.model_validate(sample["event"]).data)
    response = ApprovalResponse.model_validate(sample["response"])
    assert approval.blocking is False
    assert response.approval_hash == approval.approval_hash


def test_rejection():
    sample = load("3_rejection.json")
    assert ApprovalResponse.model_validate(sample["response"]).decision == "reject"
    events = [Event.model_validate(e) for e in sample["events"]]
    assert [e.type for e in events] == ["approval.resolved", "agent.message", "task.state"]
    assert "file.changed" not in [e.type for e in events]


def test_failed_run():
    event = Event.model_validate(load("4_failed_run.json"))
    assert RunResult.model_validate(event.data).exit_code == 1
    assert event.announce.priority == "high"


def test_successful_run():
    event = Event.model_validate(load("5_successful_run.json"))
    assert RunResult.model_validate(event.data).stdout_tail == "Hello, World!\n"


def test_security_block():
    findings, request = [Event.model_validate(e) for e in load("6_security_block.json")]
    assert findings.type == "security.findings"
    assert Approval.model_validate(request.data).blocking is True


def test_blocking_defaults_to_false():
    data = load("2_write_approval.json")["event"]["data"]
    del data["blocking"]
    assert Approval.model_validate(data).blocking is False


def test_event_data_must_match_type():
    with pytest.raises(ValidationError):
        EventDraft(type="run.result", data={"state": "working"})


def test_tool_args_forbid_extra_fields():
    with pytest.raises(ValidationError):
        WriteFilesArgs.model_validate({"files": [{"path": "a.py", "content": "", "mode": "x"}]})
