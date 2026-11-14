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


# ----- the spoken text in the samples is what the gateway writes (docs/contracts.md §4) -----


def _gateway(scan=None):
    from artemis.policy.gateway import ToolGateway

    async def emit(_draft):
        pass

    return ToolGateway(None, None, emit, projects_dir=Path("."), scan=scan)


def test_write_approval_text_matches_the_gateway():
    from artemis.contracts import ProjectRef
    from artemis.policy.gateway import _Write

    async def scan(_folder):
        return []

    gateway = _gateway(scan)
    gateway._folder_pending = True  # create_project reserved the name; the first write creates it
    event = Event.model_validate(load("2_write_approval.json")["event"])
    sample = Approval.model_validate(event.data)
    built = gateway._write_approval(
        ProjectRef(slug="hello-world", root="hello-world"),
        [_Write(path="main.py", old=None, content='print("Hello, World!")\n')],
        [],
    )
    assert (built.summary, built.files, built.diff, built.blocking) == (
        sample.summary, sample.files, sample.diff, sample.blocking
    )
    assert event.announce.text == sample.summary


@pytest.mark.parametrize("name", ["4_failed_run.json", "5_successful_run.json"])
def test_run_result_text_matches_the_gateway(name):
    from artemis.policy.gateway import run_announcement

    event = Event.model_validate(load(name))
    assert event.announce.text == run_announcement("main.py", RunResult.model_validate(event.data), 10)


def test_security_block_text_matches_the_gateway():
    findings, request = [Event.model_validate(e) for e in load("6_security_block.json")]
    approval = Approval.model_validate(request.data)
    assert findings.announce is None  # the findings are spoken in the approval summary
    summary, blocking = _gateway()._summary("Run main.py with Python.", approval.findings, "run")
    assert (summary, blocking) == (approval.summary, approval.blocking)
    assert request.announce.text == approval.summary
