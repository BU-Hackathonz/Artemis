import os
import sys
from pathlib import Path

import pytest

from artemis.contracts import ProjectRef
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
    ref = project(runner, {"main.py": "import sys\nsys.stdout.write('x' * 300000)\n"})
    result = await runner.run(ref, "main.py", [])
    assert result.truncated and len(result.stdout_tail) == 64 * 1024


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
