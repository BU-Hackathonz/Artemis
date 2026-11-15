"""infra/start.sh and infra/setup-ubuntu.sh, run for real under bash.

Hermetic: a fake `uv` stands in for uv and runs a tiny HTTP server that answers /health, so there is no
network, no real core, no apt, no sudo and no real uv. Linux and bash only; the tests take a few seconds.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INFRA = REPO / "infra"
START = INFRA / "start.sh"
SETUP = INFRA / "setup-ubuntu.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or BASH is None or shutil.which("curl") is None,
    reason="needs Linux, bash and curl",
)

FAKE_KEY = "fake_value_for_test"

# A fake uv: records its arguments (never its environment), then runs the fake core on the port that is
# the last argument. FAKE_UV_MODE=hang makes it a process that never answers /health.
FAKE_UV = """#!/bin/sh
printf '%s\\n' "$@" > "$FAKE_UV_ARGV"
if [ "$FAKE_UV_MODE" = hang ]; then
    exec sleep 60
fi
for last; do :; done
exec python3 -I "$FAKE_CORE" "$last"
"""

# A fake core: answers GET /health with 200 only for the right Bearer token. It records its own PID and
# whether ANTHROPIC_API_KEY reached its environment (yes or no, never the value).
FAKE_CORE = """import http.server
import os
import sys

token = os.environ.get("ARTEMIS_TOKEN", "")
port = int(sys.argv[1])
with open(os.environ["FAKE_CORE_PID"], "w") as f:
    f.write(str(os.getpid()))
print("fake core starting", flush=True)
print("key_in_env=" + ("yes" if os.environ.get("ANTHROPIC_API_KEY") else "no"), flush=True)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path == "/health" and self.headers.get("Authorization") == "Bearer " + token
        self.send_response(200 if ok else 401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}' if ok else b'{"error": "unauthorized"}')

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


def _env(tmp_path: Path, mode: str = "serve", key: str | None = None) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    uv = bin_dir / "uv"
    uv.write_text(FAKE_UV)
    uv.chmod(0o755)
    core = tmp_path / "fake_core.py"
    core.write_text(FAKE_CORE)
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID", "XDG_RUNTIME_DIR", "XDG_STATE_HOME", "HOME")}
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
        HOME=str(tmp_path / "home"),
        XDG_RUNTIME_DIR=str(tmp_path / "run"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        FAKE_UV_ARGV=str(tmp_path / "uv_argv.txt"),
        FAKE_CORE=str(core),
        FAKE_CORE_PID=str(tmp_path / "core_pid.txt"),
        FAKE_UV_MODE=mode,
    )
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "run").mkdir(exist_ok=True)
    if key is not None:
        env["ANTHROPIC_API_KEY"] = key
    return env


def _pid_gone(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.1)
    return False


def test_both_scripts_pass_bash_syntax_check():
    for script in (START, SETUP):
        result = subprocess.run([BASH, "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck is not installed")
def test_shellcheck_is_clean():
    for script in (START, SETUP):
        result = subprocess.run(["shellcheck", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr


def test_setup_help_works_and_touches_nothing(tmp_path):
    env = _env(tmp_path)
    result = subprocess.run([BASH, str(SETUP), "--help"], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert "Usage:" in result.stdout
    assert "--no-apt" in result.stdout and "--extension-dir" in result.stdout
    assert not (tmp_path / "uv_argv.txt").exists()


def test_start_help_works(tmp_path):
    result = subprocess.run([BASH, str(START), "--help"], env=_env(tmp_path), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert "--agent stub|claude" in result.stdout


def test_claude_without_a_key_and_no_terminal_fails_without_prompting(tmp_path):
    result = subprocess.run(
        [BASH, str(START), "--agent", "claude", "--voice", "stub"],
        env=_env(tmp_path), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert "needs it" in result.stderr


def test_launch_file_is_private_has_no_key_and_is_removed_on_exit(tmp_path):
    env = _env(tmp_path, key=FAKE_KEY)
    out_path = tmp_path / "out.txt"
    with out_path.open("w") as out:
        proc = subprocess.Popen(
            [BASH, str(START), "--agent", "claude", "--voice", "stub"],
            env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.time() + 20
        while time.time() < deadline and proc.poll() is None:
            if "Core is ready." in out_path.read_text():
                break
            time.sleep(0.2)
        assert "Core is ready." in out_path.read_text(), out_path.read_text()

        launch_dir = tmp_path / "run" / "artemis"
        launch_file = launch_dir / "launch.json"
        assert stat.S_IMODE(launch_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(launch_file.stat().st_mode) == 0o600

        text = launch_file.read_text()
        info = json.loads(text)
        assert set(info) == {"url", "token", "pid"}
        assert re.fullmatch(r"http://127\.0\.0\.1:\d+", info["url"])
        assert re.fullmatch(r"[A-Za-z0-9_-]{43}", info["token"])
        assert info["pid"] == int((tmp_path / "core_pid.txt").read_text())
        assert FAKE_KEY not in text

        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=15) == 130
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert not launch_file.exists()
    assert _pid_gone(info["pid"]), "the core was not stopped"

    # The key reached the core's environment (the fake core says yes), but not argv, the log or the launch file.
    log = (tmp_path / "state" / "artemis" / "core.log").read_text()
    assert "key_in_env=yes" in log
    assert FAKE_KEY not in log and info["token"] not in log
    argv = (tmp_path / "uv_argv.txt").read_text()
    assert FAKE_KEY not in argv and info["token"] not in argv
    assert FAKE_KEY not in out_path.read_text()


def test_failed_health_check_removes_launch_file_and_stops_the_core(tmp_path):
    env = _env(tmp_path, mode="hang")
    result = subprocess.run(
        [BASH, str(START), "--voice", "stub", "--health-timeout", "1"],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1
    assert "did not answer /health" in result.stderr + result.stdout
    assert not (tmp_path / "run" / "artemis" / "launch.json").exists()
