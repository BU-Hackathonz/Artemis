import os
import subprocess
import sys
from pathlib import Path

import pytest

from artemis.contracts import ProjectRef
from artemis.runner import local
from artemis.runner.local import LocalRunner, NoInterpreter, child_env, find_python


@pytest.fixture
def python() -> Path:
    # The test interpreter stands in for the user's Python here; production refuses the core's own.
    return Path(sys.executable)


@pytest.fixture
def runner(tmp_path, python):
    return LocalRunner(tmp_path / "projects", python=python)


def project(runner: LocalRunner, files: dict[str, str]) -> ProjectRef:
    ref = runner.create_project("demo")
    for path, content in files.items():
        runner.write_file(ref, path, content)
    return ref


async def test_hello_world(runner):
    ref = project(runner, {"main.py": 'print("Hello, World!")\n'})
    result = await runner.run(ref, "main.py", [])
    assert result.exit_code == 0 and not result.timed_out
    assert result.stdout_tail.replace("\r\n", "\n") == "Hello, World!\n"
    assert result.op_id.startswith("op_")


async def test_failure_keeps_exit_code_and_traceback(runner):
    ref = project(runner, {"main.py": "pritn('hi')\n"})
    result = await runner.run(ref, "main.py", [])
    assert result.exit_code == 1
    assert "NameError" in result.stderr_tail


async def test_timeout_kills_the_tree(runner, tmp_path):
    marker = tmp_path / "child_alive"
    child = f"import time\ntime.sleep(3)\nopen({str(marker)!r}, 'w').write('x')\n"
    code = "import subprocess, sys, time\nsubprocess.Popen([sys.executable, 'child.py'])\ntime.sleep(30)\n"
    ref = project(runner, {"main.py": code, "child.py": child})
    result = await runner.run(ref, "main.py", [], timeout_s=1)
    assert result.timed_out and result.exit_code is None
    assert result.duration_ms < 10_000
    import asyncio

    await asyncio.sleep(3.5)
    assert not marker.exists(), "a grandchild survived the kill"


async def test_no_secrets_no_stdin_no_stray_tilde(runner, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    code = (
        "import os, sys\n"
        "print('KEY' if 'ANTHROPIC_API_KEY' in os.environ else 'clean')\n"
        "print(input('x') if False else sys.stdin.read() == '')\n"
        "print(sys.flags.isolated)\n"
    )
    ref = project(runner, {"main.py": code})
    result = await runner.run(ref, "main.py", [])
    lines = result.stdout_tail.split()
    assert lines == ["clean", "True", "1"], result
    assert not (Path(ref.root) / "~").exists()


async def test_output_is_capped(runner):
    ref = project(runner, {"main.py": "import sys\nsys.stdout.write(('x' * 99 + '\\n') * 3000)\n"})
    result = await runner.run(ref, "main.py", [])
    assert result.truncated and 64 * 1024 - 100 < len(result.stdout_tail) <= 64 * 1024
    assert result.stdout_tail.startswith("\n" + "x" * 99)  # the cut-off line is dropped


async def test_a_secret_the_cut_goes_through_leaves_no_piece(runner):
    """Only the last 64 KB is kept. If the cut lands inside a secret, the rest of it no longer
    matches the secret, so redaction would miss it: the runner drops that cut-off word."""
    key = "sk-ant-api03-FAKEcutthrough0123456789abcdefghijklmnopqrstuvwxyz"
    code = (
        "import sys\n"
        f"sys.stdout.write({key!r} + ' tail\\n')\n"
        f"sys.stdout.write('w' * ({64 * 1024 + 25} - {len(key) + 6}))\n"
    )
    result = await runner.run(project(runner, {"main.py": code}), "main.py", [])
    assert result.truncated
    assert key[25:] not in result.stdout_tail and result.stdout_tail.startswith(" tail\n")


def test_writes_stay_inside_and_report_changes(runner):
    ref = runner.create_project("demo")
    first = runner.write_file(ref, "pkg/a.py", "a\nb\n")
    assert (first.action, first.lines_added) == ("created", 2)
    second = runner.write_file(ref, "pkg/a.py", "a\nc\n")
    assert (second.action, second.lines_added, second.lines_removed) == ("modified", 1, 1)
    with pytest.raises(ValueError):
        runner.write_file(ref, "../escape.py", "x")
    with pytest.raises(FileExistsError):
        runner.create_project("demo")


def test_child_env_is_explicit(python, monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "nope")
    env = child_env(python)
    assert "SECRET_TOKEN" not in env and env["PYTHONIOENCODING"] == "utf-8"
    assert env["PATH"].split(os.pathsep)[0] == str(python.parent)


def test_find_python_refuses_missing_override(tmp_path):
    with pytest.raises(NoInterpreter):
        find_python(str(tmp_path / "nope.exe"))


# ----- which Python runs user code (risk 7) -----


def test_is_core_python_refuses_core_interpreter_and_venv(tmp_path, monkeypatch):
    core_venv = (tmp_path / "core" / "venv").resolve()
    core_python = core_venv / "bin" / "python"
    base = (tmp_path / "base").resolve()
    monkeypatch.setattr(local.sys, "executable", str(core_python))
    monkeypatch.setattr(local.sys, "prefix", str(core_venv))
    monkeypatch.setattr(local.sys, "base_prefix", str(base))
    assert local._is_core_python(core_python)  # the core's own interpreter
    assert local._is_core_python(core_venv / "lib" / "helper.py")  # anything under its venv
    assert not local._is_core_python(base / "python")  # a system Python is fine
    assert not local._is_core_python(tmp_path / "elsewhere" / "python")
    # Outside a venv only the exact interpreter is refused, not everything under the prefix.
    monkeypatch.setattr(local.sys, "base_prefix", str(core_venv))
    assert not local._is_core_python(core_venv / "lib" / "helper.py")


def test_is_core_python_fails_closed_when_a_path_cannot_resolve(tmp_path, monkeypatch):
    def broken(self, strict=False):
        raise OSError("no such device")

    monkeypatch.setattr(Path, "resolve", broken)
    assert local._is_core_python(tmp_path / "python")


def test_find_python_skips_core_python_and_falls_back(tmp_path, monkeypatch):
    monkeypatch.delenv("ARTEMIS_RUN_PYTHON", raising=False)
    real = tmp_path / "py312"
    real.write_text("")
    monkeypatch.setattr(local.shutil, "which", {"uv": "/fake/uv", "python3": "/fake/python3", "python": "/fake/python"}.get)

    def fake_run(cmd, **kwargs):
        if cmd[0] == "/fake/uv":
            if "--system" in cmd:
                raise subprocess.TimeoutExpired(cmd, 15)  # _probe returns None
            raise OSError("uv vanished")  # _probe returns None
        if cmd[0] == "/fake/python3":  # the core's own interpreter, which must be skipped
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{sys.executable}\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{real}\n", stderr="")

    monkeypatch.setattr(local.subprocess, "run", fake_run)
    assert find_python() == real

    monkeypatch.setattr(local.shutil, "which", lambda name: None)
    with pytest.raises(NoInterpreter, match="couldn't find a Python"):
        find_python()


def test_run_python_override_must_not_be_the_core_interpreter(monkeypatch):
    monkeypatch.setenv("ARTEMIS_RUN_PYTHON", sys.executable)
    with pytest.raises(NoInterpreter):
        find_python()


def test_a_separate_venv_override_is_allowed(tmp_path, monkeypatch):
    """A separate venv's python links to the same base interpreter as the core's; that's not ours."""
    other = tmp_path / "other-venv" / "bin" / "python"
    other.parent.mkdir(parents=True)
    try:
        other.symlink_to(Path(sys.executable).resolve())
    except (OSError, NotImplementedError):
        pytest.skip("can't create a symlink here")
    monkeypatch.setenv("ARTEMIS_RUN_PYTHON", str(other))
    assert find_python() == other


# ----- Windows branches, exercised on Linux by monkeypatching IS_WINDOWS -----


def test_windows_child_env_passes_only_the_minimal_set(python, monkeypatch):
    monkeypatch.setattr(local, "IS_WINDOWS", True)
    monkeypatch.setenv("SYSTEMROOT", r"D:\WinRoot")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("USERPROFILE", r"C:\Users\avery")
    monkeypatch.setenv("TEMP", r"C:\Users\avery\AppData\Local\Temp")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("TMP", raising=False)
    env = child_env(python)
    assert "ANTHROPIC_API_KEY" not in env
    assert env["PYTHONIOENCODING"] == "utf-8" and env["SYSTEMROOT"] == r"D:\WinRoot"
    assert env["PATH"] == os.pathsep.join([str(python.parent), str(Path(r"D:\WinRoot") / "System32")])
    # The profile and temp keys keep Python's caches out of a literal '~' folder; unset keys are not added.
    assert env["USERPROFILE"] == r"C:\Users\avery" and env["TEMP"].endswith("Temp")
    assert "APPDATA" not in env and "TMP" not in env and "HOME" not in env
    assert set(env) <= {"PYTHONIOENCODING", "SYSTEMROOT", "PATH", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP"}


class _FakeProc:
    pid = 4242

    def __init__(self, returncode=None):
        self.returncode = returncode
        self.killed = 0

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed += 1


def test_windows_tree_kill_uses_taskkill_not_killpg(monkeypatch):
    monkeypatch.setattr(local, "IS_WINDOWS", True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-FAKEtaskkill")
    ran = []

    def fake_run(cmd, **kwargs):
        ran.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0)

    def no_killpg(*args):
        raise AssertionError("killpg is POSIX-only")

    monkeypatch.setattr(local.subprocess, "run", fake_run)
    monkeypatch.setattr(local.os, "killpg", no_killpg, raising=False)  # absent on Windows
    proc = _FakeProc()
    local._kill_tree(proc)
    assert ran[0][0] == ["taskkill", "/T", "/F", "/PID", "4242"]
    assert ran[0][1]["stdin"] == subprocess.DEVNULL
    assert "ANTHROPIC_API_KEY" not in ran[0][1]["env"] and "PATH" in ran[0][1]["env"]  # never os.environ
    assert proc.killed == 1


@pytest.mark.parametrize("failure", [OSError("taskkill missing"), subprocess.TimeoutExpired("taskkill", 10)])
def test_windows_tree_kill_failure_still_kills_the_process(monkeypatch, failure):
    monkeypatch.setattr(local, "IS_WINDOWS", True)

    def failing_run(cmd, **kwargs):
        raise failure

    monkeypatch.setattr(local.subprocess, "run", failing_run)
    proc = _FakeProc()
    local._kill_tree(proc)  # no exception escapes the runner
    assert proc.killed == 1


def test_kill_tree_does_nothing_once_the_process_has_exited(monkeypatch):
    monkeypatch.setattr(local, "IS_WINDOWS", True)
    monkeypatch.setattr(local.subprocess, "run", lambda *a, **k: pytest.fail("taskkill should not run"))
    proc = _FakeProc(returncode=0)
    local._kill_tree(proc)
    assert proc.killed == 0
def test_probes_and_git_init_get_no_secrets(tmp_path, monkeypatch):
    """Interpreter probes and `git init` run with the core's tools only, never the key or the token (finding 3)."""
    from types import SimpleNamespace

    import artemis.runner.local as local

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("ARTEMIS_TOKEN", "token-should-not-leak")
    monkeypatch.delenv("ARTEMIS_RUN_PYTHON", raising=False)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(local.subprocess, "run", fake_run)
    monkeypatch.setattr(local.shutil, "which", lambda name: f"/fake/bin/{name}")
    with pytest.raises(NoInterpreter):
        find_python()
    LocalRunner(tmp_path / "projects", python=Path(sys.executable)).create_project("demo")

    assert any(cmd[:2] == ["git", "init"] for cmd, _ in calls)
    assert any(cmd[0] == "/fake/bin/python3" for cmd, _ in calls)
    for cmd, kwargs in calls:
        env = kwargs["env"]
        assert "ANTHROPIC_API_KEY" not in env and "ARTEMIS_TOKEN" not in env, cmd
        assert "PATH" in env, cmd


# ----- cancel: "Canceled" means the program has stopped -----


def _gone(pid: int) -> bool:
    """The process no longer exists, or is a zombie nobody has reaped yet (it runs no code)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except FileNotFoundError:
        return True


@pytest.mark.skipif(local.IS_WINDOWS or not Path("/proc").exists(), reason="POSIX process group, /proc")
async def test_cancel_kills_the_tree_before_it_writes(runner, tmp_path):
    import asyncio

    marker = tmp_path / "written_after_cancel"
    pids = tmp_path / "pids"
    child = f"import time\ntime.sleep(1)\nopen({str(marker)!r}, 'w').write('child')\n"
    code = (
        "import os, subprocess, sys, time\n"
        "kid = subprocess.Popen([sys.executable, 'child.py'])\n"
        f"open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{kid.pid}}')\n"
        "time.sleep(1)\n"
        f"open({str(marker)!r}, 'w').write('main')\n"
        "time.sleep(30)\n"
    )
    ref = project(runner, {"main.py": code, "child.py": child})
    job = asyncio.create_task(runner.run(ref, "main.py", [], timeout_s=10))
    for _ in range(500):  # both processes are up
        if pids.exists() and len(pids.read_text().split()) == 2:
            break
        await asyncio.sleep(0.01)
    main_pid, child_pid = map(int, pids.read_text().split())

    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job, timeout=2)  # the cancel completes promptly

    for _ in range(100):  # SIGKILL is delivered asynchronously; allow it a moment
        if _gone(main_pid) and _gone(child_pid):
            break
        await asyncio.sleep(0.01)
    assert _gone(main_pid) and _gone(child_pid), "the program outlived the cancel"
    await asyncio.sleep(2)
    assert not marker.exists(), f"written after the cancel by {marker.read_text()}"


async def test_cancel_kills_with_the_timeout_helper_and_survives_its_failure(runner, monkeypatch):
    """Windows path with fakes: a cancel runs taskkill /T /F on the program, and a kill that raises
    doesn't stop the cancel from completing."""
    import asyncio
    import threading

    ref = project(runner, {"main.py": "print('hi')\n"})  # before faking subprocess (git init)
    monkeypatch.setattr(local, "IS_WINDOWS", True)
    started, release = threading.Event(), threading.Event()
    killed = []

    class Proc(_FakeProc):
        stdout = stderr = None

        def wait(self, timeout=None):
            started.set()
            release.wait(10)  # the program "runs" until it is killed
            self.returncode = 1
            return 1

        def kill(self):
            super().kill()
            release.set()

    procs = []

    def fake_popen(argv, **kwargs):
        assert kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
        procs.append(Proc())
        return procs[-1]

    def fake_run(cmd, **kwargs):
        killed.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    def no_killpg(*args):
        raise AssertionError("killpg is POSIX-only")

    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x8000000, raising=False)
    monkeypatch.setattr(local.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(local.subprocess, "run", fake_run)
    monkeypatch.setattr(local.os, "killpg", no_killpg, raising=False)  # absent on Windows
    monkeypatch.setattr(local, "_Tail", lambda pipe: type("T", (), {"truncated": False, "text": lambda s: ""})())

    async def cancel_mid_run():
        job = asyncio.create_task(runner.run(ref, "main.py", []))
        assert await asyncio.to_thread(started.wait, 5)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(job, timeout=2)

    await cancel_mid_run()
    assert killed == [["taskkill", "/T", "/F", "/PID", "4242"]]
    assert procs[0].killed == 1

    # The kill itself fails: the cancel still completes (and the program is still killed directly).
    started.clear()
    release.clear()

    def broken_kill_tree(proc):
        raise RuntimeError("taskkill exploded")

    monkeypatch.setattr(local, "_kill_tree", broken_kill_tree)
    await cancel_mid_run()
    assert procs[1].killed == 1
