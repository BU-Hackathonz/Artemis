"""Tool argument models and the tool result (contracts.md §3.2, design B3 and B4).

The same models validate arguments in the policy gate and generate the JSON Schema sent to
the model, so every one forbids extra fields.
"""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ErrorCode = Literal[
    "invalid_args",
    "path_rejected",
    "rejected_by_user",
    "invalidated",
    "scan_blocked",
    "timeout",
    "not_found",
    "tool_failed",
    "echo_unavailable",
    "scan_unavailable",
    "plan_required",  # a tool other than plan was called before the request's plan (§10)
    "desktop_unavailable",  # typing needs the island, and it isn't connected (§9)
]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProjectArgs(_Args):
    name: str = Field(min_length=1, max_length=80)


class OpenProjectArgs(_Args):
    name: str = Field(min_length=1, max_length=80)


class ListFilesArgs(_Args):
    dir: str | None = None


class ReadFileArgs(_Args):
    path: str
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class FileWrite(_Args):
    path: str
    content: str


class WriteFilesArgs(_Args):
    files: list[FileWrite] = Field(min_length=1)


class RunProgramArgs(_Args):
    entry: str
    args: list[str] = []


class Edit(_Args):
    old: str
    new: str


class EditFileArgs(_Args):
    """Search/replace edits to one existing file. Each `old` must match exactly once."""

    path: str
    edits: list[Edit] = Field(min_length=1)


class InspectPageArgs(_Args):
    path: str  # an .html or .htm file in the current project


class ScanProjectArgs(_Args):
    """A security scan on request. Read-only, so it needs no approval."""

    path: str | None = None  # a file or folder in the current project; None scans all of it


# ----- desktop tools (contracts.md §9, Proposed). The gate handles each (policy/desktop.py), so
# they are in TOOL_ARGS below too, with the plan.


class OpenAppArgs(_Args):
    app: str = Field(min_length=1, max_length=120)  # a desktop id ("code.desktop") or an app name ("VS Code")


class OpenPathArgs(_Args):
    path: str = Field(min_length=1, max_length=1024)  # under the home folder, or relative to the project
    with_app: str | None = Field(default=None, max_length=120)  # e.g. "code.desktop"; None = its default app


class ReadPathArgs(_Args):
    path: str = Field(min_length=1, max_length=1024)  # under the home folder; the denylist applies
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _lines_in_order(self):
        if self.start_line is not None and self.end_line is not None and self.end_line < self.start_line:
            raise ValueError("end_line must not be before start_line")
        return self


class RunCommandArgs(_Args):
    command: str = Field(min_length=1, max_length=2000)  # one shell command line, shown word for word
    cwd: str | None = Field(default=None, max_length=1024)  # default: the current project


class TypeTextArgs(_Args):
    text: str = Field(min_length=1, max_length=4000)  # typed into the focused window by the island


class PressKeysArgs(_Args):
    # e.g. "ctrl+s", "Return", "ctrl+shift+grave": modifiers joined with +, then one key name.
    keys: str = Field(min_length=1, max_length=60, pattern=r"^((ctrl|shift|alt)\+){0,3}[A-Za-z0-9_]{1,20}$")


# ----- the plan (contracts.md §10, Proposed): the agent's first tool call for a request.

PlanAction = Literal["think", "read", "write", "run", "open", "type", "explain"]


class PlanStep(_Args):
    say: str = Field(min_length=1, max_length=200)  # one short spoken sentence for this step
    action: PlanAction
    target: str | None = Field(default=None, max_length=120)  # "editor", "terminal", "file", "browser", or an app


class PlanArgs(_Args):
    goal: str = Field(min_length=1, max_length=200)
    steps: list[PlanStep] = Field(min_length=1, max_length=12)


DESKTOP_TOOL_ARGS: dict[str, type[BaseModel]] = {
    "open_app": OpenAppArgs,
    "open_path": OpenPathArgs,
    "read_path": ReadPathArgs,
    "run_command": RunCommandArgs,
    "type_text": TypeTextArgs,
    "press_keys": PressKeysArgs,
}

PLAN_ARGS: dict[str, type[BaseModel]] = {"plan": PlanArgs}


TOOL_ARGS: dict[str, type[BaseModel]] = {
    "create_project": CreateProjectArgs,
    "open_project": OpenProjectArgs,
    "list_files": ListFilesArgs,
    "read_file": ReadFileArgs,
    "write_files": WriteFilesArgs,
    "run_program": RunProgramArgs,
    "edit_file": EditFileArgs,
    "inspect_page": InspectPageArgs,
    "scan_project": ScanProjectArgs,
    **DESKTOP_TOOL_ARGS,
    **PLAN_ARGS,
}


class ToolResult(BaseModel):
    ok: bool
    data: Any = None
    error: ErrorCode | None = None
    message: str | None = None

    def to_model_text(self) -> str:
        """The JSON string the model sees as the tool result."""
        if self.ok:
            return json.dumps({"ok": True, "data": self.data}, ensure_ascii=False)
        return json.dumps(
            {"ok": False, "error": self.error, "message": self.message or ""},
            ensure_ascii=False,
        )
