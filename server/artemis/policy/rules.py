"""The policy table: what each tool needs before it may run (design B3).

There is no delete, network or install tool, and the one shell tool (run_command) runs a single approved line
without the API keys. A tool that isn't in this table doesn't exist.
"""

import sys
from dataclasses import dataclass

from pydantic import BaseModel

from artemis.contracts import (
    ApprovalKind,
    CreateProjectArgs,
    EditFileArgs,
    InspectPageArgs,
    ListFilesArgs,
    OpenAppArgs,
    OpenPathArgs,
    PlanArgs,
    PressKeysArgs,
    PublishGithubArgs,
    ReadPathArgs,
    RunCommandArgs,
    TypeTextArgs,
    OpenProjectArgs,
    ReadFileArgs,
    RunProgramArgs,
    ScanProjectArgs,
    WriteFilesArgs,
    DESKTOP_TOOL_ARGS,
)


@dataclass(frozen=True)
class Rule:
    args: type[BaseModel]
    approval_kind: ApprovalKind | None = None
    scan_before: bool = False

    @property
    def needs_approval(self) -> bool:
        return self.approval_kind is not None


RULES: dict[str, Rule] = {
    # Reserves the name only; the folder appears with the first approved write (design B2).
    "create_project": Rule(CreateProjectArgs),
    "open_project": Rule(OpenProjectArgs),
    "list_files": Rule(ListFilesArgs),
    "read_file": Rule(ReadFileArgs),
    "write_files": Rule(WriteFilesArgs, approval_kind="write_files", scan_before=True),
    "run_program": Rule(RunProgramArgs, approval_kind="run_program", scan_before=True),
    # Search/replace edits to an existing file, in any project (decisions.md §9).
    "edit_file": Rule(EditFileArgs, approval_kind="write_files", scan_before=True),
    # Echo Check on a page in the project: read-only, so no approval. Results are untrusted.
    "inspect_page": Rule(InspectPageArgs),
    # The same Bandit scan, when the developer asks for it. Read-only, so no approval.
    "scan_project": Rule(ScanProjectArgs),
    # The request's plan (contracts.md §10): no approval, it only says what will happen.
    "plan": Rule(PlanArgs),
    # The desktop (contracts.md §9): every one waits for the developer.
    "open_app": Rule(OpenAppArgs, approval_kind="open"),
    "open_path": Rule(OpenPathArgs, approval_kind="open"),
    "read_path": Rule(ReadPathArgs, approval_kind="read_outside"),
    "run_command": Rule(RunCommandArgs, approval_kind="run_command"),
    "type_text": Rule(TypeTextArgs, approval_kind="type"),
    "press_keys": Rule(PressKeysArgs, approval_kind="type"),
    # Outward-facing: the code leaves the laptop. Keyboard only, never by voice (contracts.md §5.4).
    "publish_github": Rule(PublishGithubArgs, approval_kind="publish"),
}


def desktop_supported() -> bool:
    """The six desktop tools (contracts.md section 9) are built on bash, gtk-launch, gio and the XDG
    folders, so they exist on Linux only. Elsewhere (Windows: SAPI/NVDA client) they are neither offered
    to the model nor run; the plan stays."""
    return sys.platform.startswith("linux")


def rule_for(name: str) -> "Rule | None":
    """The rule for a tool, or None when it doesn't exist here (a desktop tool off Linux)."""
    if name in DESKTOP_TOOL_ARGS and not desktop_supported():
        return None
    return RULES.get(name)
