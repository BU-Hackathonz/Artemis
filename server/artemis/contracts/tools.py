"""Tool argument models and the tool result (contracts.md §3.2, design B3 and B4).

The same models validate arguments in the policy gate and generate the JSON Schema sent to
the model, so every one forbids extra fields.
"""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ErrorCode = Literal[
    "invalid_args",
    "path_rejected",
    "rejected_by_user",
    "invalidated",
    "scan_blocked",
    "timeout",
    "not_found",
    "tool_failed",
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


TOOL_ARGS: dict[str, type[BaseModel]] = {
    "create_project": CreateProjectArgs,
    "open_project": OpenProjectArgs,
    "list_files": ListFilesArgs,
    "read_file": ReadFileArgs,
    "write_files": WriteFilesArgs,
    "run_program": RunProgramArgs,
    "edit_file": EditFileArgs,
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
