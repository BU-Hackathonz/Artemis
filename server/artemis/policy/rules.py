"""The policy table: what each tool needs before it may run (design B3).

There is no shell, delete, network or install tool. A tool that isn't in this table doesn't exist.
"""

from dataclasses import dataclass

from pydantic import BaseModel

from artemis.contracts import (
    ApprovalKind,
    CreateProjectArgs,
    EditFileArgs,
    ListFilesArgs,
    OpenProjectArgs,
    ReadFileArgs,
    RunProgramArgs,
    WriteFilesArgs,
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
}
