"""The security scan that runs before every write approval and every run approval
(design D2, D3). Bandit is the gate; the model only explains what it found.
"""

import asyncio
import glob
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from artemis.contracts import Finding, Severity

from .paths import SKIP_DIRS, project_files

TIMEOUT_S = 20
# Our own (empty) Bandit settings. Without --ini, Bandit reads a .bandit file from the folder it
# scans, so a project (or a planted file) could switch tests off; with it, only ours apply.
_INI = Path(__file__).with_name("bandit.ini")
OUTSIDE = "(outside the project) "
# No API keys. The profile folders are needed: without them Python on Windows writes its
# caches into a literal "~" folder under the current directory.
_ENV_NAMES = ("SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA")

# Bandit severity → confidence → our level. A high level blocks the approval.
_LEVELS: dict[str, dict[str, Severity]] = {
    "HIGH": {"HIGH": "high", "MEDIUM": "high", "LOW": "medium"},
    "MEDIUM": {"HIGH": "medium", "MEDIUM": "medium", "LOW": "low"},
    "LOW": {"HIGH": "low", "MEDIUM": "low", "LOW": "low"},
}


class ScanError(Exception):
    """The scan could not run. The gateway treats this as blocked, never as clean."""


def available() -> bool:
    return importlib.util.find_spec("bandit") is not None


def parse_bandit(output: str, folder: Path, expected: set[str] | None = None) -> list[Finding]:
    """Findings from Bandit's JSON report, worst first, with paths relative to `folder`.

    `expected` is the set of files (relative, posix) that should have been read. Any of
    them missing from the report's per-file metrics was not scanned, and is reported
    like a file Bandit couldn't parse, so no difference between how we list files and
    how Bandit excludes them can end in a clean result.
    """
    try:
        report = json.loads(output)
        results = report["results"]
        errors = report.get("errors") or []
        metrics = report.get("metrics") or {}
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ScanError("Bandit did not return a report.") from exc

    def relative(filename: str) -> str:
        path = Path(filename)
        try:
            return path.resolve().relative_to(folder.resolve()).as_posix()
        except ValueError:
            # A file behind a link that points outside the folder. Never give it the bare
            # name, which could be a different, clean file in the project.
            return OUTSIDE + path.name

    findings = []
    # A file Bandit couldn't parse was not scanned. Say so, rather than let the approval
    # claim there are no findings in code nobody looked at.
    for item in errors:
        reason = str(item.get("reason") or "unknown reason")
        findings.append(
            Finding(
                rule="SCAN",
                severity="medium",
                path=relative(str(item.get("filename") or "unknown file")),
                line=1,
                message=f"couldn't be scanned: {reason}",
            )
        )
    if expected is not None:
        seen = {os.path.normcase(relative(name)) for name in metrics if name != "_totals"}
        seen |= {os.path.normcase(f.path) for f in findings}  # unparseable files were reported above
        for rel in sorted(expected):
            if os.path.normcase(rel) not in seen:
                findings.append(
                    Finding(
                        rule="SCAN",
                        severity="medium",
                        path=rel,
                        line=1,
                        message="couldn't be scanned: the scanner skipped this file",
                    )
                )
    for item in results:
        rel = relative(item["filename"])
        level = _LEVELS.get(item["issue_severity"], {}).get(item["issue_confidence"], "medium")
        findings.append(
            Finding(
                rule=item["test_id"],
                severity=level,
                path=rel,
                line=item["line_number"],
                message=item["issue_text"],
            )
        )
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(findings, key=lambda f: (order[f.severity], f.path, f.line))


def _run_bandit(folder: Path, only: Path | None = None) -> str:
    # Bandit is our own tool, so it runs on the core's interpreter, with a minimal
    # environment: no API keys reach it.
    env = {name: os.environ[name] for name in _ENV_NAMES if name in os.environ}
    # Don't read a virtual environment or node_modules before every run. The patterns are
    # anchored to the scanned folder and end in /*: Bandit also treats each entry as a
    # plain substring of the path, so a bare "<root>/.git" would silently skip
    # "<root>/.github", and an unanchored "*/node_modules/*" would skip a whole project
    # that happens to live under a folder of that name.
    folder = folder.resolve()
    root = glob.escape(str(folder))
    excluded = ",".join(
        pattern
        for name in sorted(SKIP_DIRS)
        for pattern in (f"{root}/{name}/*", f"{root}/*/{name}/*")
    )
    target = str(only.resolve() if only else folder)
    try:
        done = subprocess.run(
            [
                sys.executable, "-m", "bandit", "-r", target, "-f", "json", "-q", "-x", excluded,
                # The project can't turn findings off: no .bandit from the folder, no `# nosec`.
                "--ini", str(_INI), "--ignore-nosec",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_S,
            env=env,
            stdin=subprocess.DEVNULL,
            cwd=tempfile.gettempdir(),  # never the project folder
        )
    except subprocess.TimeoutExpired as exc:
        raise ScanError("Bandit timed out.") from exc
    # Exit code 1 means findings; anything else without a report is a failure.
    if done.returncode not in (0, 1):
        raise ScanError(f"Bandit exited with code {done.returncode}.")
    return done.stdout


async def scan(folder: Path, only: Path | None = None) -> list[Finding]:
    """Scan every Python file under `folder`, or just the file or folder `only` inside it.
    Paths in the findings are relative to `folder` either way."""
    # subprocess.run in a thread, not asyncio's subprocess: uvicorn --reload on Windows uses
    # an event loop that can't start subprocesses (design D6).
    folder = Path(folder)
    only = Path(only) if only else None
    output = await asyncio.to_thread(_run_bandit, folder, only)
    return parse_bandit(output, folder, _expected(folder, only))


def _expected(folder: Path, only: Path | None) -> set[str]:
    """The Python files Bandit should read: the same listing the gateway counts and speaks."""
    if only is not None and not only.is_dir():
        try:
            names = [only.resolve().relative_to(folder.resolve()).as_posix()]
        except ValueError:
            names = []
    else:
        names = project_files(folder, only) if folder.is_dir() else []
    return {name for name in names if name.lower().endswith((".py", ".pyw"))}
