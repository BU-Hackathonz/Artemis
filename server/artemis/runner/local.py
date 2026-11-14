"""LocalRunner: the real Runner for Windows (and Linux/macOS for tests)."""

import asyncio
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from artemis.contracts import FileChange, ProjectRef, RunResult

log = logging.getLogger(__name__)

MAX_OUTPUT = 64 * 1024  # bytes kept per stream (the tail)
IS_WINDOWS = os.name == "nt"


class NoInterpreter(Exception):
    """No Python to run user code with. The message is spoken."""


def _is_core_python(path: Path) -> bool:
    """Never run user code with the core's own interpreter or its venv (risk 7)."""
    try:
        resolved = path.resolve()
    except OSError:
        return True
    if resolved == Path(sys.executable).resolve():
        return True
    venv = Path(sys.prefix).resolve()
    return sys.prefix != sys.base_prefix and venv in resolved.parents


def _is_core_venv_path(path: Path) -> bool:
    """The core's own python, or anything inside the core's venv, by path as given (no link resolving)."""
    given = Path(os.path.abspath(path))
    if given == Path(os.path.abspath(sys.executable)):
        return True
    return sys.prefix != sys.base_prefix and Path(os.path.abspath(sys.prefix)) in given.parents


def _probe(cmd: list[str], cwd: Path) -> Path | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15, cwd=cwd, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = out.stdout.strip().splitlines()[-1:] if out.returncode == 0 else []
    return Path(line[0]) if line else None


def find_python(override: str | None = None) -> Path:
    """ARTEMIS_RUN_PYTHON, else `uv python find --system`, else `py -3`, else python3 on PATH."""
    override = override or os.environ.get("ARTEMIS_RUN_PYTHON")
    if override:
        path = Path(override)
        if not path.exists():
            raise NoInterpreter(f"ARTEMIS_RUN_PYTHON points to {override}, which doesn't exist. Fix or unset it, then restart Artemis.")
        if _is_core_venv_path(path):
            # The override must not undo risk 7 either: user code never runs in the core's own venv.
            # Compared without resolving links: a separate venv's python links to the same base
            # interpreter as ours, and that is fine (programs run with -I).
            raise NoInterpreter("ARTEMIS_RUN_PYTHON points to Artemis's own Python. Point it at a separate Python, or unset it, then restart Artemis.")
        return path
    home = Path.home()  # outside any project, so uv doesn't pick the core's venv
    candidates: list[list[str]] = []
    if shutil.which("uv"):
        candidates += [["uv", "python", "find", "--system"], ["uv", "python", "find", "--no-project"]]
    if IS_WINDOWS and shutil.which("py"):
        candidates.append(["py", "-3", "-c", "import sys; print(sys.executable)"])
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            candidates.append([found, "-c", "import sys; print(sys.executable)"])
    for cmd in candidates:
        path = _probe(cmd, home)
        if path and path.exists() and not _is_core_python(path):
            return path
    raise NoInterpreter("I couldn't find a Python to run your program with. Install Python 3.12, then restart Artemis.")


def child_env(python: Path) -> dict[str, str]:
    """The explicit, minimal environment for user programs: never os.environ, so no API keys."""
    env: dict[str, str] = {"PYTHONIOENCODING": "utf-8"}
    if IS_WINDOWS:
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        env["SYSTEMROOT"] = system_root
        env["PATH"] = os.pathsep.join([str(python.parent), str(Path(system_root) / "System32")])
        # Without these, Python on Windows writes its caches into a literal '~' folder in the cwd
        # (found by David's Fable, board post on M2).
        for key in ("USERPROFILE", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP"):
            if os.environ.get(key):
                env[key] = os.environ[key]
    else:
        env["PATH"] = os.pathsep.join([str(python.parent), "/usr/bin", "/bin"])
        for key in ("HOME", "TMPDIR"):
            if os.environ.get(key):
                env[key] = os.environ[key]
    return env


class _Tail:
    """Reads a pipe on a thread and keeps only the last MAX_OUTPUT bytes."""

    def __init__(self, pipe):
        self.buf = bytearray()
        self.truncated = False
        self._thread = threading.Thread(target=self._read, args=(pipe,), daemon=True)
        self._thread.start()

    def _read(self, pipe) -> None:
        try:
            for chunk in iter(lambda: pipe.read(8192), b""):
                self.buf += chunk
                if len(self.buf) > MAX_OUTPUT:
                    del self.buf[: len(self.buf) - MAX_OUTPUT]
                    self.truncated = True
        except (OSError, ValueError):
            pass

    def text(self, wait_s: float = 2.0) -> str:
        self._thread.join(wait_s)
        return bytes(self.buf).decode("utf-8", errors="replace")


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
                stdin=subprocess.DEVNULL,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        proc.kill()
    except OSError:
        pass


class LocalRunner:
    def __init__(self, projects_dir: Path, python: Path | None = None):
        self.projects_dir = Path(projects_dir)
        self._python = python  # found lazily, once; a missing Python is reported at run time

    def _op(self) -> str:
        return f"op_{uuid.uuid4().hex[:12]}"

    @property
    def python(self) -> Path:
        if self._python is None:
            self._python = find_python()
        return self._python

    def create_project(self, name: str) -> ProjectRef:
        """`name` is already slugged by the gateway; creates exactly projects_dir/name."""
        root = self.projects_dir / name
        root.mkdir(parents=True, exist_ok=False)
        if shutil.which("git"):
            try:
                subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True, timeout=15, stdin=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                log.warning("git init failed in %s", root)
        return ProjectRef(slug=name, root=str(root))

    def _inside(self, project: ProjectRef, path: str) -> Path:
        root = Path(project.root).resolve()
        target = (root / path).resolve()
        if target != root and root not in target.parents:
            raise ValueError("path escapes the project")  # the gateway already checked; belt and braces
        return target

    def write_file(self, project: ProjectRef, path: str, content: str) -> FileChange:
        target = self._inside(project, path)
        old = target.read_text(encoding="utf-8").splitlines() if target.exists() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))  # bytes, so newlines aren't translated
        new = content.splitlines()
        return FileChange(
            op_id=self._op(),
            path=path,
            action="created" if old is None else "modified",
            lines_added=len(new) if old is None else len([line for line in new if line not in old]),
            lines_removed=0 if old is None else len([line for line in old if line not in new]),
        )

    async def run(self, project: ProjectRef, entry: str, args: list[str], timeout_s: int = 10) -> RunResult:
        # A thread, not asyncio subprocesses: uvicorn --reload's loop on Windows can't start them.
        return await asyncio.to_thread(self._run_sync, project, entry, list(args), timeout_s)

    def _run_sync(self, project: ProjectRef, entry: str, args: list[str], timeout_s: int) -> RunResult:
        self._inside(project, entry)
        python = self.python
        argv = [str(python), "-I", "-X", "utf8", entry, *args]
        kwargs: dict = {}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True  # own process group, so the whole tree can be killed
        started = time.monotonic()
        proc = subprocess.Popen(
            argv,
            cwd=project.root,
            env=child_env(python),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            **kwargs,
        )
        out, err = _Tail(proc.stdout), _Tail(proc.stderr)
        timed_out = False
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout, stderr = out.text(), err.text()
        if not IS_WINDOWS and not timed_out:
            try:  # grandchildren that outlive the program are still killed
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        return RunResult(
            op_id=self._op(),
            preset="python",
            exit_code=None if timed_out else proc.returncode,
            timed_out=timed_out,
            duration_ms=duration_ms,
            stdout_tail=stdout,
            stderr_tail=stderr,
            truncated=out.truncated or err.truncated,
        )
