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
    name: str = Field(
        min_length=1,
        max_length=80,
        description="The project name as the developer says it, up to 80 characters. It becomes the folder name, so \"hello world\" becomes hello-world.",
    )


class OpenProjectArgs(_Args):
    name: str = Field(
        min_length=1,
        max_length=80,
        description="The name of an existing project, up to 80 characters, as the developer says it.",
    )


class ListFilesArgs(_Args):
    dir: str | None = Field(
        default=None,
        description="A folder relative to the project root, for example src. Null lists the whole project.",
    )


class ReadFileArgs(_Args):
    path: str = Field(description="A file path relative to the project root, for example main.py or src/utils.py.")
    start_line: int | None = Field(
        default=None,
        ge=1,
        description="The first line to return, counted from 1. Null with end_line null returns the whole file.",
    )
    end_line: int | None = Field(
        default=None,
        ge=1,
        description="The last line to return, counted from 1 and included. Null means the end of the file.",
    )
    aloud: bool = Field(
        default=False,
        description="True when the developer asked to hear the code read to them: the tool speaks a few lines "
        "itself, in words, and returns next_start_line to continue from. False for reading to work on the file.",
    )
    line_numbers: bool = Field(
        default=False,
        description="With aloud true: True when the developer asked for line numbers to be spoken.",
    )


class FileWrite(_Args):
    path: str = Field(
        description="A file path relative to the project root, for example main.py or tests/test_main.py. "
        "Missing folders are created. Text and source files only: code, web, config and docs (.py .js .ts .jsx .tsx .html .css .scss .svg .json .yaml .toml .md .txt and similar). Shell scripts and .env files are refused.",
    )
    content: str = Field(
        description="The complete new text of the file, at most 256 kilobytes. Not a diff.",
    )


class WriteFilesArgs(_Args):
    files: list[FileWrite] = Field(
        min_length=1,
        description="Every file of one change, at most 20 files. They are shown to the developer and written together.",
    )


class RunProgramArgs(_Args):
    entry: str = Field(
        description="The path of a Python file in the project, relative to the project root, for example main.py.",
    )
    args: list[str] = Field(
        default=[],
        description="Command-line arguments, one string each. Pass an empty list when there are none.",
    )


class Edit(_Args):
    old: str = Field(
        description="The exact text to find, copied from read_file including indentation. It must occur exactly once in the file.",
    )
    new: str = Field(description="The text that replaces old. May be empty to delete old.")


class EditFileArgs(_Args):
    """Search/replace edits to one existing file. Each `old` must match exactly once."""

    path: str = Field(description="An existing file in the project, relative to the project root, for example main.py.")
    edits: list[Edit] = Field(
        min_length=1,
        description="The edits, applied in order. Each one works on the text left by the edits before it.",
    )


class InspectPageArgs(_Args):
    path: str = Field(
        description="An .html or .htm file in the project, relative to the project root, for example index.html.",
    )  # an .html or .htm file in the current project
    describe: bool = Field(
        default=False,
        description="True when the developer asked what the page looks like or contains: the tool also speaks its "
        "structure (landmarks, headings, links, form fields, images without alt text).",
    )


class ScanProjectArgs(_Args):
    """A security scan on request. Read-only, so it needs no approval."""

    path: str | None = Field(
        default=None,
        description="A file or folder in the project, relative to the project root. Null scans the whole project.",
    )  # a file or folder in the current project; None scans all of it


# ----- desktop tools (contracts.md §9, Proposed). The gate handles each (policy/desktop.py), so
# they are in TOOL_ARGS below too, with the plan.


class OpenAppArgs(_Args):
    app: str = Field(
        min_length=1,
        max_length=120,
        description="An app name such as VS Code or Terminal, or a desktop id such as code.desktop.",
    )


class OpenUrlArgs(_Args):
    url: str = Field(
        min_length=8,
        max_length=2000,
        description="A web address starting with https:// or http://, for example https://www.google.com or "
        "https://github.com/owner/repo. Opens in the developer's default browser.",
    )


class OpenPathArgs(_Args):
    path: str = Field(
        min_length=1,
        max_length=1024,
        description="A file or folder under the developer's home folder, given as an absolute path, a path starting with ~/, "
        "or a path relative to the current project. Script files such as .py are refused, so only documents and folders open.",
    )
    with_app: str | None = Field(
        default=None,
        max_length=120,
        description="The app to open it in, by name such as VS Code or by desktop id such as code.desktop. "
        "Null opens it in its default app. Terminals and interpreters are refused.",
    )


class ReadPathArgs(_Args):
    path: str = Field(
        min_length=1,
        max_length=1024,
        description="A text file under the developer's home folder, as an absolute path, a path starting with ~/, "
        "or a path relative to the current project. Private folders such as dot-folders in home are refused.",
    )
    start_line: int | None = Field(
        default=None,
        ge=1,
        description="The first line to return, counted from 1. Null with end_line null returns the whole file.",
    )
    end_line: int | None = Field(
        default=None,
        ge=1,
        description="The last line to return, counted from 1 and included. Null means the end of the file.",
    )

    @model_validator(mode="after")
    def _lines_in_order(self):
        if self.start_line is not None and self.end_line is not None and self.end_line < self.start_line:
            raise ValueError("end_line must not be before start_line")
        return self


class RunCommandArgs(_Args):
    command: str = Field(
        min_length=1,
        max_length=2000,
        description="One shell command line, run with bash. The developer sees it word for word before it runs.",
    )
    cwd: str | None = Field(
        default=None,
        max_length=1024,
        description="A folder under the developer's home folder. Null runs the command in the current project.",
    )


class PublishGithubArgs(_Args):
    """Publish the current project as a new GitHub repository (contracts.md §9, Proposed): git init,
    one commit, gh repo create --push. Keyboard approval only; public needs the override key."""

    repo: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$",
                      description="The new repository's name only, for example tip-calculator. It is always created in "
                                  "the developer's own GitHub account.")
    public: bool = Field(default=False, description="true only when the developer asked for a public repository. "
                                                    "Private otherwise.")
    description: str | None = Field(default=None, max_length=200,
                                    description="One short sentence for the repository's description, or null.")


class TypeTextArgs(_Args):
    text: str = Field(
        min_length=1,
        max_length=4000,
        description="The text to type into the window that has the keyboard focus, up to 4000 characters. "
        "For a shortcut such as ctrl+s, use press_keys instead.",
    )


class PressKeysArgs(_Args):
    # e.g. "ctrl+s", "Return", "ctrl+shift+grave": modifiers joined with +, then one key name.
    keys: str = Field(
        min_length=1,
        max_length=60,
        pattern=r"^((ctrl|shift|alt)\+){0,3}[A-Za-z0-9_]{1,20}$",
        description="Modifiers joined with +, then one key name, for example ctrl+s, Return or ctrl+shift+grave.",
    )


# ----- the plan (contracts.md §10, Proposed): the agent's first tool call for a request.

PlanAction = Literal["think", "read", "write", "run", "open", "type", "explain"]


class PlanStep(_Args):
    say: str = Field(
        min_length=1,
        max_length=200,
        description="One short spoken sentence for this step, at most 200 characters. The developer hears it.",
    )
    action: PlanAction = Field(
        description="What the step does: think, read, write, run, open, type or explain.",
    )
    target: str | None = Field(
        default=None,
        max_length=120,
        description="Where the step happens: editor, terminal, file, browser, an app name such as VS Code, or null.",
    )


class PlanArgs(_Args):
    goal: str = Field(
        min_length=1,
        max_length=200,
        description="The developer's request in one short sentence, at most 200 characters.",
    )
    steps: list[PlanStep] = Field(
        min_length=1,
        max_length=12,
        description="The steps in the order you will take them, at most 12. Each step is spoken and shown in the panel.",
    )


DESKTOP_TOOL_ARGS: dict[str, type[BaseModel]] = {
    "open_app": OpenAppArgs,
    "open_path": OpenPathArgs,
    "open_url": OpenUrlArgs,
    "read_path": ReadPathArgs,
    "run_command": RunCommandArgs,
    "type_text": TypeTextArgs,
    "press_keys": PressKeysArgs,
    "publish_github": PublishGithubArgs,
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
