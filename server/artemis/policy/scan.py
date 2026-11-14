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
        results = json.loads(output)["results"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ScanError("Bandit did not return a report.") from exc
    findings = []
    for item in results:
        path = Path(item["filename"])
        try:
            rel = path.resolve().relative_to(folder.resolve()).as_posix()
        except ValueError:
            rel = path.name
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
    try:
        done = subprocess.run(
            [sys.executable, "-m", "bandit", "-r", str(folder), "-f", "json", "-q"],
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
