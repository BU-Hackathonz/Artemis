"""Approvals: what the user is asked to approve, and what they decided (contracts.md §2)."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel

ApprovalKind = Literal["write_files", "run_program"]
FileAction = Literal["created", "modified", "deleted"]
Severity = Literal["low", "medium", "high"]


class FileSummary(BaseModel):
    path: str
    action: FileAction
    lines_added: int
    lines_removed: int


class RunSpec(BaseModel):
    preset: Literal["python"]
    entry: str
    timeout_s: int = 10


class Finding(BaseModel):
    rule: str
    severity: Severity
    path: str
    line: int
    message: str


class Approval(BaseModel):
    approval_id: str
    kind: ApprovalKind
    approval_hash: str
    summary: str
    files: list[FileSummary] = []
    diff: str | None = None
    run: RunSpec | None = None
    findings: list[Finding] = []
    # True when a high-severity finding applies: the client's default action is
    # Reject and approving needs a distinct key.
    blocking: bool = False


class Decision(str, Enum):
    approve = "approve"
    reject = "reject"
    invalidated = "invalidated"


class ApprovalResponse(BaseModel):
    """Body of POST /approvals/{aid}."""

    decision: Literal["approve", "reject"]
    approval_hash: str
