"""The security scan that runs before every write approval and every run approval
(design D2, D3). Bandit is the gate; the model only explains what it found.
"""

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from artemis.contracts import Finding, Severity

from .paths import SKIP_DIRS

TIMEOUT_S = 20
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


def parse_bandit(output: str, folder: Path) -> list[Finding]:
    try:
        report = json.loads(output)
        results = report["results"]
        errors = report.get("errors") or []
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ScanError("Bandit did not return a report.") from exc

    def relative(filename: str) -> str:
        path = Path(filename)
        try:
            return path.resolve().relative_to(folder.resolve()).as_posix()
        except ValueError:
            return path.name

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


def _run_bandit(folder: Path) -> str:
    # Bandit is our own tool, so it runs on the core's interpreter, with a minimal
    # environment: no API keys reach it.
    env = {name: os.environ[name] for name in _ENV_NAMES if name in os.environ}
    # Don't crawl a virtual environment or node_modules before every run.
    excluded = ",".join(str(folder / name) for name in sorted(SKIP_DIRS))
    try:
        done = subprocess.run(
            [sys.executable, "-m", "bandit", "-r", str(folder), "-f", "json", "-q", "-x", excluded],
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


async def scan(folder: Path) -> list[Finding]:
    """Scan every Python file under `folder`. Paths in the findings are relative to it."""
    # subprocess.run in a thread, not asyncio's subprocess: uvicorn --reload on Windows uses
    # an event loop that can't start subprocesses (design D6).
    output = await asyncio.to_thread(_run_bandit, Path(folder))
    return parse_bandit(output, Path(folder))
