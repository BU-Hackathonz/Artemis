"""The tool gateway: validate → policy → approval → re-validate → execute.

Everything the agent does goes through `ToolGateway.call`. Writes and runs pause here until
the user decides, and what they approved is bound by hash to the exact content, so "the agent
can't write or run without approval" holds whatever the model says.

One gateway per session: it holds the session's current project.
"""

import asyncio
import difflib
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel, ValidationError

from artemis.agent.untrusted import wrap
from artemis.contracts import (
    AgentMessageData,
    Announce,
    Approval,
    ApprovalResolvedData,
    Approvals,
    CreateProjectArgs,
    Decision,
    EditFileArgs,
    Emit,
    ErrorData,
    EventDraft,
    FileSummary,
    FileWrite,
    InspectPageArgs,
    Finding,
    ListFilesArgs,
    OpenProjectArgs,
    Priority,
    ProjectRef,
    ReadFileArgs,
    Runner,
    RunProgramArgs,
    ScanProjectArgs,
    RunResult,
    RunSpec,
    SecurityFindings,
    TaskStateData,
    ToolResult,
    WriteFilesArgs,
)

from .hashing import approval_hash, run_payload, write_payload
from .paths import (
    MAX_FILES_PER_BATCH,
    SKIP_DIRS,
    PathRejected,
    check_read_size,
    check_write_size,
    is_reserved_name,
    project_files,
    safe_resolve,
)
from .redact import redact, redact_obj
from .rules import RULES

# Scans a folder and returns findings with paths relative to it. None until M2 (Bandit).
# scan_project also passes a second argument: the one file or folder inside it to read.
Scan = Callable[..., Awaitable[list[Finding]]]

MAX_LIST_ENTRIES = 200
MAX_SCAN_FINDINGS = 50
SPOKEN_OUTPUT_CHARS = 200
_SEVERITY_ORDER = ("high", "medium", "low")


class _ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _Write(BaseModel):
    """One file of a write, checked against the disk as it is right now."""

    path: str  # relative to the project root, forward slashes
    old: bytes | None  # None when the file doesn't exist yet
    content: str


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-") or "project"
    return f"{slug}-project" if is_reserved_name(slug) else slug


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def findings_sentence(findings: list[Finding]) -> str:
    """"1 high-severity issue, main.py line 4, subprocess call with shell=True." Worst first."""
    for severity in _SEVERITY_ORDER:
        group = [f for f in findings if f.severity == severity]
        if group:
            first = group[0]
            more = len(findings) - 1
            text = (
                f"{plural(len(group), f'{severity}-severity issue')}, "
                f"{first.path} line {first.line}, {first.message.rstrip('.')}"
            )
            return text + (f", and {more} more." if more else ".")
    return "No security findings."


def scan_announcement(findings: list[Finding], files: int) -> str:
    """What the user hears after scan_project. Counts by level, then the worst one."""
    scanned = plural(files, "Python file")
    if not findings:
        return f"No security findings in {scanned}."
    levels = ", ".join(
        f"{count} {level}"
        for level in _SEVERITY_ORDER
        if (count := sum(f.severity == level for f in findings))
    )
    worst = findings[0]
    return (
        f"Found {plural(len(findings), 'security issue')} in {scanned}: {levels}. "
        f"The worst is {worst.path} line {worst.line}, {worst.message.rstrip('.')}."
    )


def run_announcement(entry: str, result: RunResult, timeout_s: int) -> str:
    """What the user hears after a run. Written here, not by the model, so a failure is
    never softened (design C3)."""
    if result.timed_out:
        return f"{entry} was stopped after {timeout_s} seconds because it took too long."
    if result.exit_code == 0:
        output = " ".join(result.stdout_tail.split())
        if not output:
            return f"{entry} ran successfully. It printed nothing."
        if len(output) > SPOKEN_OUTPUT_CHARS:
            output = output[:SPOKEN_OUTPUT_CHARS].rstrip() + ", and more"
        return f"{entry} ran successfully. It printed: {output}"
    lines = [line.strip() for line in result.stderr_tail.splitlines() if line.strip()]
    text = f"{entry} failed with exit code {result.exit_code}."
    if lines:
        text += f" {lines[-1].rstrip('.')}."
    if "EOFError" in result.stderr_tail:
        text += " The program asked for input, which isn't supported yet."
    return text


def unified_diff(path: str, old: str | None, new: str) -> tuple[str, int, int]:
    """Return (diff text, lines added, lines removed) for one file."""

    def lines(text: str) -> list[str]:
        out = text.splitlines(keepends=True)
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n\\ No newline at end of file\n"
        return out

    diff = list(
        difflib.unified_diff(
            lines(old or ""),
            lines(new),
            fromfile="/dev/null" if old is None else path,
            tofile=path,
        )
    )
    body = diff[2:]
    added = sum(1 for line in body if line.startswith("+"))
    removed = sum(1 for line in body if line.startswith("-"))
    return "".join(diff), added, removed


class ToolGateway:
    def __init__(
        self,
        runner: Runner,
        approvals: Approvals,
        emit: Emit,
        *,
        projects_dir: Path,
        scan: Scan | None = None,
        run_timeout_s: int = 10,
    ):
        self._runner = runner
        self._approvals = approvals
        self._emit_event = emit
        self._projects_dir = Path(projects_dir)
        self._scan = scan
        self._run_timeout_s = run_timeout_s
        self.project: ProjectRef | None = None
        self._folder_pending = False  # reserved by create_project, not on disk yet
        self._created: set[str] = set()  # slugs of projects created in this session
        self._echo_reports: dict = {}  # path -> last Echo Check report, for before/after

    async def call(self, task_id: str, name: str, args: dict) -> ToolResult:
        rule = RULES.get(name)
        if rule is None:
            return ToolResult(ok=False, error="not_found", message=f"There is no tool named {name}.")
        try:
            parsed = rule.args.model_validate(args)
        except ValidationError as exc:
            # Field names and reasons only: never echo the values back.
            problems = "; ".join(
                f"{'.'.join(str(part) for part in err['loc']) or 'arguments'}: {err['msg']}"
                for err in exc.errors()
            )
            return ToolResult(ok=False, error="invalid_args", message=problems)

        try:
            data = await getattr(self, f"_{name}")(task_id, parsed)
        except PathRejected as exc:
            await self._error(task_id, "path_rejected", exc.reason, "Nothing was changed.")
            return ToolResult(ok=False, error="path_rejected", message=redact(exc.reason))
        except _ToolError as exc:
            return ToolResult(ok=False, error=exc.code, message=redact(exc.message))
        except Exception as exc:
            # Nothing unexpected may escape as a crash or as silence. Only the exception type
            # is reported: its message can hold absolute paths or file contents.
            # (CancelledError is not an Exception, so a cancel still propagates.)
            kind = type(exc).__name__
            await self._error(
                task_id,
                "tool_failed",
                f"Something went wrong with {name}.",
                "Nothing else was changed.",
            )
            return ToolResult(ok=False, error="tool_failed", message=f"{name} failed: {kind}.")
        return ToolResult(ok=True, data=redact_obj(data))

    # ----- events -----

    async def _emit(
        self,
        task_id: str,
        type: str,
        data: BaseModel,
        text: str | None = None,
        priority: Priority = "normal",
    ) -> None:
        announce = Announce(text=redact(text), priority=priority) if text else None
        await self._emit_event(
            EventDraft(
                task_id=task_id,
                type=type,
                announce=announce,
                data=redact_obj(data.model_dump(mode="json")),
            )
        )

    async def _say(self, task_id: str, text: str, priority: Priority = "low") -> None:
        await self._emit(task_id, "agent.message", AgentMessageData(text=text, kind="progress"), text, priority)

    async def _state(self, task_id: str, state: str, text: str | None = None) -> None:
        await self._emit(task_id, "task.state", TaskStateData(state=state), text, "low")

    async def _error(self, task_id: str, code: str, message: str, recovery: str) -> None:
        await self._emit(
            task_id,
            "error",
            ErrorData(code=code, message=message, recovery=recovery),
            f"{message} {recovery}",
            "high",
        )

    # ----- projects and reads (no approval) -----

    def _require_project(self) -> ProjectRef:
        if self.project is None:
            raise _ToolError(
                "not_found", "No project is open. Call create_project or open_project first."
            )
        return self.project

    async def _create_project(self, task_id: str, a: CreateProjectArgs) -> dict:
        base = slugify(a.name)
        slug, n = base, 1
        while (self._projects_dir / slug).exists() or slug in self._created:
            n += 1
            slug = f"{base}-{n}"
        self.project = ProjectRef(slug=slug, root=str(self._projects_dir / slug))
        self._folder_pending = True
        self._created.add(slug)
        await self._say(task_id, f"Starting project {slug}.")
        return {
            "project": slug,
            "note": "The name is reserved. The folder is created with the first approved write_files.",
        }

    async def _open_project(self, task_id: str, a: OpenProjectArgs) -> dict:
        wanted = {a.name.strip().casefold(), slugify(a.name)}
        folders = (
            sorted(p.name for p in self._projects_dir.iterdir() if p.is_dir())
            if self._projects_dir.is_dir()
            else []
        )
        match = next((f for f in folders if f.casefold() in wanted or slugify(f) in wanted), None)
        if match is None:
            listing = ", ".join(folders[:20]) or "none"
            raise _ToolError(
                "not_found",
                f"There is no project named {a.name}. Existing projects: "
                + wrap("project names", listing),
            )
        self.project = ProjectRef(slug=match, root=str(self._projects_dir / match))
        self._folder_pending = False
        await self._say(task_id, f"Opening project {match}.")
        return {"project": match}

    async def _list_files(self, task_id: str, a: ListFilesArgs) -> dict:
        project = self._require_project()
        root = Path(project.root)
        top = safe_resolve(root, a.dir) if a.dir else root
        await self._say(task_id, "Looking at the files.")
        if not top.exists() and not a.dir:
            return {"files": wrap("file names", ""), "count": 0, "truncated": False}
        if not top.is_dir():
            raise _ToolError("not_found", f"There is no folder named {a.dir}.")
        names = project_files(root, top)
        return {
            "files": wrap("file names", "\n".join(names[:MAX_LIST_ENTRIES])),
            "count": min(len(names), MAX_LIST_ENTRIES),
            "truncated": len(names) > MAX_LIST_ENTRIES,
        }

    async def _read_file(self, task_id: str, a: ReadFileArgs) -> dict:
        project = self._require_project()
        target = safe_resolve(Path(project.root), a.path)
        if not target.is_file():
            raise _ToolError("not_found", f"There is no file named {a.path}.")
        check_read_size(target, a.path)
        await self._say(task_id, f"Reading {a.path}.")
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = a.start_line or 1
        end = min(a.end_line or len(lines), len(lines))
        numbered = "\n".join(f"{n}\t{lines[n - 1]}" for n in range(start, end + 1))
        return {
            "path": a.path,
            "total_lines": len(lines),
            "content": wrap(f"file:{a.path}", numbered),
        }

    async def _inspect_page(self, task_id: str, a: InspectPageArgs) -> dict:
        """Echo Check on a page in the project: the real accessibility tree plus axe-core findings.

        Read-only, so no approval. The outline and findings come from the page, so they are
        untrusted: a page is an injection vector like any file.
        """
        project = self._require_project()
        target = safe_resolve(Path(project.root), a.path)
        if target.suffix.lower() not in (".html", ".htm"):
            raise _ToolError("invalid_args", "inspect_page only opens .html or .htm files.")
        if not target.is_file():
            raise _ToolError("not_found", f"There is no file named {a.path}.")
        check_read_size(target, a.path)
        try:
            from artemis import echo  # optional `echo` dependency group (Playwright)
        except ImportError:
            raise _ToolError(
                "echo_unavailable",
                "Echo Check isn't installed. Run: uv sync --group echo",
            ) from None
        await self._say(task_id, f"Checking {a.path} for accessibility.")
        try:
            report = await echo.echo_check(str(target))
        except echo.EchoError as exc:
            raise _ToolError("echo_unavailable", str(exc)) from None
        spoken = echo.speak_report(report)
        previous = self._echo_reports.get(a.path)
        if previous is not None:
            spoken = f"{echo.compare(previous, report)} {spoken}"
        self._echo_reports[a.path] = report
        await self._say(task_id, spoken, "normal")
        findings = "\n".join(
            f"{f.impact}: {f.rule} ({f.count}x) at {f.target}: {f.message}" for f in report.findings
        )
        return {
            "path": a.path,
            "problems": len(report.findings),
            "findings": wrap(f"page:{a.path} axe findings", findings or "none"),
            "outline": wrap(f"page:{a.path} accessibility tree", "\n".join(report.outline)),
            # Checks axe couldn't decide (e.g. text over a gradient): a person has to look.
            "needs_review": wrap(
                f"page:{a.path} needs manual review",
                "\n".join(f"{f.rule} at {f.target}: {f.message}" for f in getattr(report, "needs_review", []))
                or "none",
            ),
        }

    async def _scan_project(self, task_id: str, a: ScanProjectArgs) -> dict:
        """The security scan, on request. Read-only, so no approval.

        What the developer hears is written here, not by the model, so a scan that didn't run
        or found nothing to look at is never reported as clean.
        """
        project = self._require_project()
        root = Path(project.root)
        base = root.resolve()
        scope = "the whole project"
        top = root
        if a.path:
            top = safe_resolve(root, a.path)
            scope = top.relative_to(base).as_posix()
            # The scan never reads these folders, so it must not answer "clean" for one.
            # Checked by name before anything else, whatever the case and whether or not
            # the folder exists, so the answer is the same on every file system.
            skipped = {name.casefold() for name in SKIP_DIRS}
            inside = next((part for part in scope.split("/") if part.casefold() in skipped), None)
            if inside:
                raise _ToolError(
                    "invalid_args",
                    f"{a.path} is inside {inside}, which the security scan never checks. "
                    "Nothing was scanned.",
                )
            if not top.exists():
                raise _ToolError("not_found", f"There is no file or folder named {a.path}.")
        if self._scan is None:
            message = "The security scanner isn't installed."
            await self._error(task_id, "scan_unavailable", message, "Nothing was scanned.")
            raise _ToolError("scan_unavailable", f"{message} Run: uv sync --group security")

        # The files Bandit reads: Python files, not behind a link, not in a skipped folder at
        # any depth. Findings for anything else are dropped, and this is the count we speak.
        listed = project_files(root, top) if top.is_dir() else [scope]
        python = {name for name in listed if name.lower().endswith((".py", ".pyw"))}
        where = project.slug if not a.path else scope
        if not python:
            text = f"There are no Python files to scan in {where}. The scan only checks Python."
            await self._say(task_id, text, "normal")
            return {"scope": scope, "files_scanned": 0, "note": text}

        await self._say(task_id, f"Scanning {where} for security problems.")
        try:
            # Only the requested file or folder is read, so one file in a big project is quick.
            found = await (self._scan(root, top) if a.path else self._scan(root))
        except Exception:
            message = "The security scan could not run."
            await self._error(task_id, "scan_unavailable", message, "Nothing was scanned.")
            raise _ToolError("scan_unavailable", f"{message} Nothing was scanned.") from None
        findings = sorted(
            (f for f in found if f.path in python),
            key=lambda f: (_SEVERITY_ORDER.index(f.severity), f.path, f.line),
        )
        counts = {level: sum(f.severity == level for f in findings) for level in _SEVERITY_ORDER}
        shown = findings[:MAX_SCAN_FINDINGS]
        if shown:
            await self._emit(
                task_id,
                "security.findings",
                SecurityFindings(op_id=f"op_{uuid.uuid4().hex[:12]}", tool="bandit", findings=shown),
            )
        await self._say(task_id, scan_announcement(findings, len(python)), "normal")
        lines = "\n".join(
            f"{f.severity}: {f.rule} {f.path} line {f.line}: {f.message}" for f in shown
        )
        return {
            "scope": scope,
            "files_scanned": len(python),
            "counts": counts,
            "truncated": len(findings) > len(shown),
            # File names and messages come from the project, so they are data, not instructions.
            "findings": wrap("scan:bandit findings", lines or "none"),
            "note": "A static check of the Python files only. It does not prove the code is safe.",
        }

    # ----- approvals -----

    async def _ask(self, task_id: str, approval: Approval, nothing_done: str) -> Decision:
        """Pause for the user's decision. This await is the cancel point (design E4)."""
        if approval.findings:
            await self._emit(
                task_id,
                "security.findings",
                SecurityFindings(
                    op_id=f"op_{uuid.uuid4().hex[:12]}", tool="bandit", findings=approval.findings
                ),
            )
        await self._state(task_id, "awaiting_approval")
        await self._emit(task_id, "approval.requested", approval, approval.summary, "high")
        try:
            return await self._approvals.request(task_id, approval)
        except asyncio.CancelledError:
            await self._resolved(task_id, approval, "reject", "invalidated")
            await self._say(task_id, f"Canceled. {nothing_done}", "high")
            raise

    async def _resolved(self, task_id: str, approval: Approval, decision: str, by: str) -> None:
        await self._emit(
            task_id,
            "approval.resolved",
            ApprovalResolvedData(approval_id=approval.approval_id, decision=decision, by=by),
        )

    async def _rejected(self, task_id: str, approval: Approval, nothing_done: str) -> None:
        await self._resolved(task_id, approval, "reject", "user")
        await self._state(task_id, "working")
        if approval.blocking:
            raise _ToolError(
                "scan_blocked",
                f"The user rejected it after the security scan blocked it. {nothing_done} "
                + findings_sentence(approval.findings),
            )
        raise _ToolError("rejected_by_user", f"The user rejected it. {nothing_done}")

    def _summary(self, action: str, findings: list[Finding], verb: str) -> tuple[str, bool]:
        """Return (summary, blocking). Written from the tool arguments, not by the model."""
        blocking = any(f.severity == "high" for f in findings)
        if blocking:
            return (
                f"Blocked: {findings_sentence(findings)} {action} "
                "Escape to reject, or use the override key to approve anyway.",
                True,
            )
        # Only claim "no findings" when a scan actually ran.
        scanned = f" {findings_sentence(findings)}" if self._scan else ""
        return f"{action}{scanned} Enter to {verb}, Escape to reject.", False

    async def _run_scan(self, task_id: str, folder: Path, nothing_done: str) -> list[Finding]:
        if self._scan is None:
            return []
        try:
            return await self._scan(folder)
        except Exception:
            message = "The security scan could not run."
            await self._error(task_id, "scan_blocked", message, nothing_done)
            raise _ToolError("scan_blocked", f"{message} {nothing_done}") from None

    # ----- write_files -----

    def _plan_write(self, project: ProjectRef, files: list[FileWrite]) -> list[_Write]:
        """Check every file against the disk as it is now. Called before asking and again
        before writing."""
        root = Path(project.root)
        base = root.resolve()
        plan: dict[str, _Write] = {}
        for file in files:
            target = safe_resolve(root, file.path, for_write=True)
            check_write_size(file.content, file.path)
            rel = target.relative_to(base).as_posix()
            if os.path.normcase(rel) in plan:
                raise _ToolError("invalid_args", f"{rel} is listed more than once.")
            old = None
            if target.exists():
                if not target.is_file():
                    raise PathRejected(f"{rel} is a folder, not a file.")
                if project.slug not in self._created:
                    raise PathRejected(
                        f"{rel} already exists, and I can only overwrite files in a project "
                        "created in this session."
                    )
                old = target.read_bytes()
            plan[os.path.normcase(rel)] = _Write(path=rel, old=old, content=file.content)
        return list(plan.values())

    def _write_hash(self, project: ProjectRef, plan: list[_Write]) -> str:
        payload = write_payload([(w.path, w.old, w.content) for w in plan])
        return approval_hash("write_files", project.slug, payload)

    def _write_approval(
        self, project: ProjectRef, plan: list[_Write], findings: list[Finding]
    ) -> Approval:
        summaries, diffs, parts = [], [], []
        for w in plan:
            old_text = None if w.old is None else w.old.decode("utf-8", errors="replace")
            diff, added, removed = unified_diff(w.path, old_text, w.content)
            diffs.append(diff)
            if w.old is None:
                action = "created"
                parts.append(f"{w.path}, {plural(added, 'line')}")
            else:
                action = "modified"
                parts.append(f"modify {w.path}, {plural(added, 'line')} added, {removed} removed")
            summaries.append(
                FileSummary(path=w.path, action=action, lines_added=added, lines_removed=removed)
            )
        count = plural(len(plan), "file")
        listing = "; ".join(parts)
        if self._folder_pending:
            action = f"Create project {project.slug} with {count}: {listing}."
        elif all(w.old is None for w in plan):
            action = f"Create {count} in {project.slug}: {listing}."
        else:
            action = f"Change {count} in {project.slug}: {listing}."
        summary, blocking = self._summary(action, findings, "approve")
        return Approval(
            approval_id=f"apr_{uuid.uuid4().hex[:12]}",
            kind="write_files",
            approval_hash=self._write_hash(project, plan),
            summary=summary,
            files=summaries,
            diff="".join(diffs),
            findings=findings,
            blocking=blocking,
        )

    async def _scan_proposed(self, task_id: str, plan: list[_Write]) -> list[Finding]:
        """Scan the proposed files in a temp folder outside the workspace, so the user hears
        the risk before anything touches the project (design D2)."""
        if self._scan is None:
            return []
        temp = Path(tempfile.mkdtemp(prefix="artemis-scan-"))
        try:
            for w in plan:
                target = temp / w.path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(w.content.encode("utf-8"))
            return await self._run_scan(task_id, temp, "Nothing was written.")
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    async def _write_files(self, task_id: str, a: WriteFilesArgs) -> dict:
        project = self._require_project()
        if len(a.files) > MAX_FILES_PER_BATCH:
            raise _ToolError(
                "invalid_args", f"At most {MAX_FILES_PER_BATCH} files can be written at once."
            )
        return await self._commit(task_id, project, lambda: self._plan_write(project, a.files))

    async def _commit(self, task_id: str, project: ProjectRef, make_plan) -> dict:
        """Scan, ask, re-validate and write. `make_plan` checks the change against the disk as
        it is at that moment; it is called before asking and again right before writing."""
        plan = make_plan()
        findings = await self._scan_proposed(task_id, plan)

        for _attempt in range(2):
            approval = self._write_approval(project, plan, findings)
            decision = await self._ask(task_id, approval, "Nothing was written.")
            if decision is Decision.reject:
                await self._rejected(task_id, approval, "Nothing was written.")
            # Re-validate right before executing: the files may have changed while the
            # user was deciding.
            plan = make_plan()
            if decision is Decision.approve and self._write_hash(project, plan) == approval.approval_hash:
                await self._resolved(task_id, approval, "approve", "user")
                break
            await self._resolved(task_id, approval, "reject", "invalidated")
        else:
            await self._state(task_id, "working")
            raise _ToolError(
                "invalidated",
                "The files changed twice while waiting for approval. Nothing was written.",
            )

        # No await between the first and the last write, so a cancel can't split the batch.
        changes = []
        try:
            if self._folder_pending:
                self.project = project = self._runner.create_project(project.slug)
                self._folder_pending = False
            for w in plan:
                changes.append(self._runner.write_file(project, w.path, w.content))
        except Exception as exc:
            done = ", ".join(c.path for c in changes) or "nothing"
            message = f"Writing failed: {type(exc).__name__}. Already written: {done}."
            await self._error(task_id, "tool_failed", message, "Check the project folder and try again.")
            await self._state(task_id, "working")
            raise _ToolError("tool_failed", message) from None

        try:
            for change in changes:
                verb = "Created" if change.action == "created" else "Changed"
                text = f"{verb} {change.path}, {plural(change.lines_added, 'line')}."
                await self._emit(task_id, "file.changed", change, text)
            await self._state(task_id, "working")
        except asyncio.CancelledError:
            written = ", ".join(c.path for c in changes)
            await self._say(task_id, f"Canceled. Already written: {written}.", "high")
            raise
        return {
            "project": project.slug,
            "files": [c.model_dump(exclude={"op_id"}) for c in changes],
            "findings": [f.model_dump() for f in findings],
        }

    # ----- edit_file -----

    def _plan_edit(self, project: ProjectRef, a: EditFileArgs) -> list[_Write]:
        """Apply search/replace edits to the file as it is on disk right now. Each `old` must
        match exactly once, so the developer approves an unambiguous change."""
        root = Path(project.root)
        target = safe_resolve(root, a.path, for_write=True)
        if not target.is_file():
            raise _ToolError("not_found", f"There is no file named {a.path}.")
        check_read_size(target, a.path)
        rel = target.relative_to(root.resolve()).as_posix()
        old = target.read_bytes()
        try:
            text = old.decode("utf-8")
        except UnicodeDecodeError:
            raise PathRejected(f"{rel} is not a UTF-8 text file, so I can't edit it.") from None
        # Match the file's own line endings, whichever the model used.
        crlf = "\r\n" in text

        def endings(value: str) -> str:
            value = value.replace("\r\n", "\n")
            return value.replace("\n", "\r\n") if crlf else value

        for number, edit in enumerate(a.edits, start=1):
            find, replacement = endings(edit.old), endings(edit.new)
            count = text.count(find) if find else 0
            if count == 0:
                raise _ToolError(
                    "invalid_args",
                    f"Edit {number}: the old text was not found in {rel}. "
                    "Read the file again and copy the text exactly.",
                )
            if count > 1:
                raise _ToolError(
                    "invalid_args",
                    f"Edit {number}: the old text appears {count} times in {rel}. "
                    "Include more surrounding lines so it matches once.",
                )
            text = text.replace(find, replacement)
        if text.encode("utf-8") == old:
            raise _ToolError("invalid_args", f"The edits don't change {rel}.")
        check_write_size(text, rel)
        return [_Write(path=rel, old=old, content=text)]

    async def _edit_file(self, task_id: str, a: EditFileArgs) -> dict:
        project = self._require_project()
        return await self._commit(task_id, project, lambda: self._plan_edit(project, a))

    # ----- run_program -----

    def _entry(self, project: ProjectRef, entry: str) -> tuple[str, bytes]:
        root = Path(project.root)
        target = safe_resolve(root, entry)
        if not target.is_file():
            raise _ToolError("not_found", f"There is no file named {entry}.")
        if target.suffix.lower() != ".py":
            raise _ToolError("invalid_args", f"I can only run Python files, and {entry} isn't one.")
        check_read_size(target, entry)
        return target.relative_to(root.resolve()).as_posix(), target.read_bytes()

    async def _run_program(self, task_id: str, a: RunProgramArgs) -> dict:
        project = self._require_project()
        nothing = "Nothing was run."
        self._entry(project, a.entry)

        for _attempt in range(2):
            findings = await self._run_scan(task_id, Path(project.root), nothing)
            entry, content = self._entry(project, a.entry)
            digest = approval_hash("run_program", project.slug, run_payload(entry, a.args, content))
            action = f"Run {entry} with Python"
            action += f", with arguments {' '.join(a.args)}." if a.args else "."
            summary, blocking = self._summary(action, findings, "run")
            approval = Approval(
                approval_id=f"apr_{uuid.uuid4().hex[:12]}",
                kind="run_program",
                approval_hash=digest,
                summary=summary,
                run=RunSpec(preset="python", entry=entry, timeout_s=self._run_timeout_s),
                findings=findings,
                blocking=blocking,
            )
            decision = await self._ask(task_id, approval, nothing)
            if decision is Decision.reject:
                await self._rejected(task_id, approval, nothing)
            entry, content = self._entry(project, a.entry)
            current = approval_hash("run_program", project.slug, run_payload(entry, a.args, content))
            if decision is Decision.approve and current == digest:
                await self._resolved(task_id, approval, "approve", "user")
                break
            await self._resolved(task_id, approval, "reject", "invalidated")
        else:
            await self._state(task_id, "working")
            raise _ToolError(
                "invalidated", f"{a.entry} changed twice while waiting for approval. {nothing}"
            )

        await self._state(task_id, "running", f"Running {entry}.")
        try:
            result = await self._runner.run(project, entry, list(a.args), self._run_timeout_s)
        except asyncio.CancelledError:
            await self._say(task_id, f"Canceled while running {entry}.", "high")
            raise
        except Exception as exc:
            message = f"{entry} could not be started: {type(exc).__name__}."
            await self._error(task_id, "tool_failed", message, "Try running it again.")
            await self._state(task_id, "working")
            raise _ToolError("tool_failed", message) from None

        spoken = run_announcement(entry, result, self._run_timeout_s)
        await self._emit(task_id, "run.result", result, spoken, "high")
        await self._state(task_id, "working")
        if result.timed_out:
            raise _ToolError("timeout", spoken)
        return {
            "entry": entry,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "stdout": wrap(f"program:{entry} stdout", result.stdout_tail),
            "stderr": wrap(f"program:{entry} stderr", result.stderr_tail),
            "output_truncated": result.truncated,
        }
