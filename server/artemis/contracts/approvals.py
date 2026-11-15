"""Approvals: what the user is asked to approve, and what they decided (contracts.md §2)."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel

ApprovalKind = Literal["write_files", "run_program", "open", "read_outside", "run_command", "type"]
# Desktop approvals (contracts.md §9, Proposed): open an app or a path, read a file outside the
# project, run one shell command, type into the focused app.
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


class DesktopSpec(BaseModel):
    """What a desktop approval will do, shown and spoken word for word before it is approved."""

    action: Literal["open_app", "open_path", "read_path", "run_command", "type_text", "press_keys"]
    # The app id, the resolved path, the command's resolved folder, or for typing the focused
    # window as the island reported it ("org.gnome.Ptyxis: ~/project"). The hash covers it.
    target: str
    text: str | None = None  # the exact command, the exact text to type, or the keys
    sha256: str | None = None  # read_path and open_path: the file's content, so a swapped file fails


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
    desktop: DesktopSpec | None = None  # set for the desktop kinds (§9)
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
