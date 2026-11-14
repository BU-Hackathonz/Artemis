"""Secrets never leak: an end-to-end sweep with fake secrets in the core's environment.

The core is artemis.core.main, loaded by uvicorn the way infra/start.ps1 runs it, with its stdout
and stderr going to core.log. Everything else is real too: the runner (programs really run), the
gateway, Bandit, approvals over HTTP, StubVoice's Announcer, and ClaudeAgent on the real SDK
client built at startup. Only the network is replaced: a scripted model answers each request and
keeps what it was sent.

It runs the demo flows (docs/demo.md 1 to 4; 5 needs a browser and has its own test below) and
the failure paths, then looks for each fake secret, and for any 12 characters in a row of one,
everywhere a secret could get out: the event bus, every SSE payload, everything spoken, core.log,
the session logs, every HTTP response, every approval and hash input, every request to the model,
and the environment of every process the core starts.
"""

import asyncio
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

anthropic = pytest.importorskip("anthropic")
import httpx  # noqa: E402  (the test's own HTTP client, dev group)
import httpx2  # noqa: E402  (the SDK's HTTP library: the scripted model is its MockTransport)
import uvicorn  # noqa: E402

from artemis.agent import claude as claude_module  # noqa: E402
from artemis.agent.claude import ClaudeAgent  # noqa: E402
from artemis.childenv import is_secret_name  # noqa: E402
from artemis.core.approvals import SessionApprovals  # noqa: E402
from artemis.policy import gateway as gateway_module  # noqa: E402
from artemis.runner.local import LocalRunner  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[2] / "fixture"

# Obviously fake, in the shape of the real thing. The launch token is made per run, like start.ps1 does.
KEY = "sk-ant-api03-FAKE7sDQPqA3djAdSJowinHhkI5FyGdIAFNJ9B_Yzf-VRdl-w5s8YkBLaVsqEL9oHT7nglU7G441dfnzFzUCFqbHGs-1fAA"
WORKSPACE = "wrkspc_FAKEb467cd7be0943bd85da7eb92"
PIECE = 12  # any 12 characters in a row of a secret count as a leak
FINAL = {"succeeded", "failed", "canceled"}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def browser_available() -> bool:
    """Same places Echo Check looks (echo/test_check.py)."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    if os.environ.get("ARTEMIS_CHROME"):
        return True
    custom = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    return (
        (bool(custom) and Path(custom).exists())
        or (Path.home() / ".cache" / "ms-playwright").exists()
        or local.exists()
        or bool(shutil.which("google-chrome") or shutil.which("chrome") or shutil.which("msedge"))
    )


# ----- the scripted model -----


def reply(*blocks) -> tuple[int, dict]:
    stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    return 200, {
        "id": f"msg_{uuid.uuid4().hex[:12]}", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": list(blocks), "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def tool(tool_name: str, /, **args) -> tuple[int, dict]:
    return reply({"type": "tool_use", "id": f"toolu_{uuid.uuid4().hex[:12]}", "name": tool_name, "input": args})


def answer(text: str) -> tuple[int, dict]:
    return reply({"type": "text", "text": text})


def api_error(status: int, kind: str, message: str) -> tuple[int, dict]:
    return status, {"type": "error", "error": {"type": kind, "message": message}}


class ScriptedModel:
    """The Messages API, offline: each request gets the next scripted step, and is kept."""

    def __init__(self):
        self.steps: list[tuple[int, dict]] = []
        self.requests: list[tuple[str, dict[str, str], str]] = []  # url, headers, body

    def handle(self, request):
        self.requests.append((str(request.url), dict(request.headers), request.content.decode("utf-8")))
        status, body = self.steps.pop(0)
        return httpx2.Response(status, json=body)


# ----- one launch of the core -----


class Launch:
    """The core as infra/start.ps1 starts it, and a client that approves the way a person would:
    Enter on a normal approval, Escape on a blocked one."""

    LOGGERS = ("", "uvicorn", "uvicorn.error", "uvicorn.access")

    def __init__(self, tmp_path: Path, monkeypatch):
        self.mp = monkeypatch
        self.token = secrets.token_urlsafe(32)  # New-LaunchToken: 32 random bytes, base64url
        self.values = {"ANTHROPIC_API_KEY": KEY, "ANTHROPIC_WORKSPACE_ID": WORKSPACE, "ARTEMIS_TOKEN": self.token}
        self.auth = {"Authorization": f"Bearer {self.token}"}
        self.projects = tmp_path / "projects"
        self.logs = tmp_path / "logs"
        self.model = ScriptedModel()
        self.events: list[dict] = []  # every SSE event, parsed
        self.sse: list[str] = []  # every SSE line, raw
        self.spoken: list[str] = []  # every text handed to the voice
        self.responses: list[str] = []  # every HTTP response: status, headers, body
        self.decisions: list[int] = []  # HTTP status of each POST /approvals the client sent
        self.approvals: list[str] = []  # every Approval the gateway asked for, in full
        self.hash_inputs: list[str] = []  # everything an approval hash was computed from
        self.runs: list[tuple[str, str, object]] = []  # (project, entry, RunResult) as the runner returned it
        self.children: list[tuple[str, dict[str, str]]] = []  # (program, environment) of every child process
        self.fail_next_announce = False
        self._tasks: list[asyncio.Task] = []

    # ----- start and stop (inside the test itself: pytest swaps stdout and stderr between phases) -----

    async def __aenter__(self) -> "Launch":
        try:
            await self.start()
        except BaseException:
            await self.stop()
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    async def start(self) -> None:
        mp = self.mp
        for name in list(os.environ):
            if name.startswith("ANTHROPIC_") or name in ("ARTEMIS_RUNNER", "ARTEMIS_CONVERSATION", "ARTEMIS_TOKEN"):
                mp.delenv(name)
        for name, value in self.values.items():
            mp.setenv(name, value)
        mp.setenv("ARTEMIS_AGENT", "claude")
        mp.setenv("ARTEMIS_VOICE", "stub")
        mp.setenv("ARTEMIS_LOG_DIR", str(self.logs))
        mp.setenv("ARTEMIS_PROJECTS_DIR", str(self.projects))
        self._demo_projects()

        # core.log: start.ps1 sends the core's stdout and stderr there (`> core.log 2>&1`).
        self.logs.mkdir(parents=True)
        self._std = sys.stdout, sys.stderr
        self._core_log = (self.logs / "core.log").open("a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = self._core_log
        # uvicorn configures its own loggers and leaves the root logger alone, so the core's
        # install_log_redaction adds the root handler itself. Put logging back afterwards.
        self._logging = {
            name: (lg.handlers[:], lg.level, lg.propagate, lg.disabled)
            for name in self.LOGGERS
            for lg in [logging.getLogger(name or None)]
        }
        logging.getLogger().handlers.clear()

        self._record()
        real = anthropic.AsyncAnthropic

        def offline(**kwargs):  # no key here: the SDK reads ANTHROPIC_API_KEY itself, as in production
            return real(
                max_retries=0,
                http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(self.model.handle)),
                **kwargs,
            )

        mp.setattr(claude_module.anthropic, "AsyncAnthropic", offline)
        mp.setattr(claude_module, "RETRY_DELAY_S", 0)

        mp.delitem(sys.modules, "artemis.core.main", raising=False)  # built on import, from this environment
        port = free_port()
        self.server = uvicorn.Server(uvicorn.Config("artemis.core.main:app", host="127.0.0.1", port=port))
        self.serving = asyncio.create_task(self.server.serve())
        while not self.server.started:
            if self.serving.done():
                self.serving.result()  # the startup error
                raise RuntimeError("uvicorn stopped before it started")
            await asyncio.sleep(0.01)
        main = sys.modules["artemis.core.main"]
        mp.setitem(sys.modules, "artemis.core.main", main)  # removed again after the test
        self.core = main.app.state.core
        self._quiet_voice()
        self.http = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=30)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if hasattr(self, "http"):
            await self.http.aclose()
        if hasattr(self, "server"):
            self.server.should_exit = True
            await asyncio.wait_for(self.serving, 15)
        for name, (handlers, level, propagate, disabled) in getattr(self, "_logging", {}).items():
            lg = logging.getLogger(name or None)
            lg.handlers[:] = handlers
            lg.setLevel(level)
            lg.propagate, lg.disabled = propagate, disabled
        if hasattr(self, "_core_log"):
            sys.stdout, sys.stderr = self._std
            self._core_log.close()


    def _demo_projects(self) -> None:
        """What infra/setup-demo.ps1 copies, plus a project of the developer's own that holds the
        same secrets in a settings file (a program can read that, and print it)."""
        self.projects.mkdir(parents=True)
        shutil.copytree(FIXTURE / "injected-readme", self.projects / "calc")
        shutil.copytree(FIXTURE / "risky-run", self.projects / "old-app")
        (self.projects / "signup-page").mkdir()
        shutil.copy(FIXTURE / "echo-page" / "before.html", self.projects / "signup-page" / "index.html")
        keyed = self.projects / "keyed-app"
        keyed.mkdir()
        (keyed / "settings.py").write_text(
            '"""Local settings for the keyed app. Not committed."""\n\n'
            "DEBUG = False\n"
            f'ANTHROPIC_API_KEY = "{KEY}"\n'
            f'ANTHROPIC_WORKSPACE_ID = "{WORKSPACE}"\n'
            f'CORE_TOKEN = "{self.token}"\n',
            encoding="utf-8",
        )
        # The programs load settings.py by path: user programs run isolated (-I), so they can't import it.
        load = 'import runpy\nimport sys\n\ns = runpy.run_path("settings.py")\n'
        # Prints the secrets where the output gets cut: the spoken line (200 characters) and the
        # head and tail the model gets of a long output (4 KB each).
        (keyed / "show.py").write_text(
            load
            + 'out = "x" * 180 + s["CORE_TOKEN"] + "\\n"\n'
            'out += "y" * (4096 - len(out) - 20) + s["ANTHROPIC_WORKSPACE_ID"] + "\\n"\n'
            'out += ("filler " * 14 + "\\n") * 40\n'
            'tail = s["ANTHROPIC_API_KEY"] + "\\n"\n'
            'out += tail + "z" * (4096 + 30 - len(tail))\n'
            "sys.stdout.write(out)\n",
            encoding="utf-8",
        )
        # A traceback that carries the key and the workspace.
        (keyed / "crash.py").write_text(
            load
            + 'raise RuntimeError(f"the API refused key {s[\'ANTHROPIC_API_KEY\']} '
            'in workspace {s[\'ANTHROPIC_WORKSPACE_ID\']}")\n',
            encoding="utf-8",
        )
        # More than the runner keeps (the last 64 KB): what it keeps starts 25 characters into the key.
        (keyed / "bigtail.py").write_text(
            load
            + 'key = s["ANTHROPIC_API_KEY"]\n'
            'sys.stdout.write(key + "\\n")\n'
            "rest = 64 * 1024 + 25 - len(key) - 1\n"
            'sys.stdout.write(("w" * 99 + "\\n") * (rest // 100) + "w" * (rest % 100))\n',
            encoding="utf-8",
        )

    def _record(self) -> None:
        """Keep what the core hands to approvals, hashes and child processes, as it hands it over."""
        mp, launch = self.mp, self
        real_request = SessionApprovals.request

        async def request(approvals, task_id, approval):
            launch.approvals.append(approval.model_dump_json())
            return await real_request(approvals, task_id, approval)

        mp.setattr(SessionApprovals, "request", request)
        real_hash = gateway_module.approval_hash

        def approval_hash(kind, workspace_id, payload):
            launch.hash_inputs.append(json.dumps([kind, workspace_id, payload]))
            return real_hash(kind, workspace_id, payload)

        mp.setattr(gateway_module, "approval_hash", approval_hash)
        real_run = LocalRunner._run_sync

        def run_sync(runner, project, entry, args, timeout_s, *rest):
            result = real_run(runner, project, entry, args, timeout_s, *rest)
            launch.runs.append((project.slug, entry, result))
            return result

        mp.setattr(LocalRunner, "_run_sync", run_sync)
        real_popen = subprocess.Popen.__init__

        def popen(proc, args, *rest, **kwargs):
            env = kwargs["env"] if "env" in kwargs else (rest[9] if len(rest) > 9 else None)
            program = args[0] if isinstance(args, (list, tuple)) else args
            launch.children.append((Path(str(program)).name, dict(os.environ if env is None else env)))
            real_popen(proc, args, *rest, **kwargs)

        mp.setattr(subprocess.Popen, "__init__", popen)  # asyncio's subprocesses use it too

    def _quiet_voice(self) -> None:
        """Keep everything handed to the voice, and never speak aloud during a test: on a laptop
        with SAPI or NVDA, use the silent speaker the core falls back to anyway (it prints each
        line to stderr, so core.log)."""
        from artemis.voice.speech import NullSpeaker

        voice = self.core.voice
        announcer = voice.announcer
        if announcer.current().name != "none":
            announcer.is_nvda_running = lambda: False
            announcer.sapi = NullSpeaker()
        real = voice.announce

        async def announce(text, priority):
            self.spoken.append(text)
            if self.fail_next_announce:  # a speech engine whose error quotes something secret
                self.fail_next_announce = False
                raise RuntimeError(f"speech engine failed: {KEY} {WORKSPACE}")
            await real(text, priority)

        voice.announce = announce  # before any session binds it to its bus

    # ----- the client -----

    async def call(self, method: str, path: str, *, auth: bool = True, headers=None, **kwargs) -> httpx.Response:
        sent = {**(self.auth if auth else {}), **(headers or {})}
        response = await self.http.request(method, path, headers=sent, **kwargs)
        self.responses.append(f"{method} {path} -> {response.status_code}\n{response.headers}\n{response.text}")
        return response

    def spawn(self, coro) -> None:
        self._tasks.append(asyncio.create_task(coro))

    async def session(self) -> str:
        sid = (await self.call("POST", "/sessions")).json()["session_id"]
        self.spawn(self._listen(sid))
        await self.wait(lambda: any(e["session_id"] == sid for e in self.events))
        return sid

    async def _listen(self, sid: str) -> None:
        async with self.http.stream("GET", f"/sessions/{sid}/events", headers=self.auth) as response:
            self.responses.append(f"GET /sessions/{sid}/events -> {response.status_code}\n{response.headers}")
            async for line in response.aiter_lines():
                self.sse.append(line)
                if line.startswith("data:"):
                    event = json.loads(line[5:])
                    self.events.append(event)
                    if event["type"] == "approval.requested":
                        self.spawn(self._decide(event["data"]))

    async def _decide(self, approval: dict) -> None:
        decision = "reject" if approval["blocking"] else "approve"
        body = {"decision": decision, "approval_hash": approval["approval_hash"]}
        self.decisions.append((await self.call("POST", f"/approvals/{approval['approval_id']}", json=body)).status_code)

    async def wait(self, predicate, timeout: float = 20.0) -> None:
        async def poll():
            while not predicate():
                await asyncio.sleep(0.01)

        await asyncio.wait_for(poll(), timeout)

    def state(self, tid: str) -> str | None:
        states = [e["data"]["state"] for e in self.events if e["type"] == "task.state" and e["task_id"] == tid]
        return states[-1] if states else None

    def said(self, tid: str) -> list[str]:
        return [e["announce"]["text"] for e in self.events if e["task_id"] == tid and e["announce"]]

    async def task(self, sid: str, text: str, *steps, expect: str = "succeeded") -> str:
        self.model.steps.extend(steps)
        body = {"client_request_id": f"cr_{uuid.uuid4().hex[:12]}", "source": "keyboard", "text": text}
        tid = (await self.call("POST", f"/sessions/{sid}/tasks", json=body)).json()["task_id"]
        await self.wait(lambda: self.state(tid) in FINAL)
        assert self.state(tid) == expect, (text, self.said(tid))
        assert not self.model.steps, (text, "the scripted model turns were not all used")
        return tid

    # ----- where secrets could get out -----

    def core_log(self) -> str:
        return (self.logs / "core.log").read_text(encoding="utf-8")

    def sinks(self) -> dict[str, str]:
        out = {
            "SSE stream": "\n".join(self.sse),
            "spoken": "\n".join(self.spoken),
            "core.log": self.core_log(),
            "HTTP responses": "\n".join(self.responses),
            "approvals": "\n".join(self.approvals),
            "approval hash inputs": "\n".join(self.hash_inputs),
        }
        for log in sorted(self.logs.glob("*.jsonl")):
            out[f"session log {log.name}"] = log.read_text(encoding="utf-8")
        for sid, session in self.core.sessions.items():
            out[f"event bus {sid}"] = json.dumps([e.model_dump(mode="json") for e in session.bus.history])
            out[f"agent conversation {sid}"] = json.dumps(session.agent.messages, default=_plain)
        for i, (url, headers, body) in enumerate(self.model.requests):
            out[f"model request {i}"] = url + "\n" + body
            sent = {k: v for k, v in headers.items() if k.lower() not in ("authorization", "x-api-key", "anthropic-workspace-id")}
            out[f"model request {i} headers"] = json.dumps(sent)
        for i, (program, env) in enumerate(self.children):
            out[f"child process {i} ({program}) environment"] = json.dumps(env)
        return out

    def leaks(self, sink: str, text: str) -> list[str]:
        found = []
        for name, secret in self.values.items():
            pieces = [secret] + [secret[i : i + PIECE] for i in range(len(secret) - PIECE + 1)]
            hit = next((p for p in pieces if p in text), None)
            if hit is None:
                continue
            at = text.index(hit)
            context = text[max(0, at - 40) : at + len(hit) + 40].replace(hit, f"<{name}>")
            what = name if hit == secret else f"{len(hit)}+ characters of {name}"
            found.append(f"{sink}: {what}: ...{context!r}...")
        return found

    def assert_no_leaks(self) -> None:
        problems = [leak for sink, text in self.sinks().items() for leak in self.leaks(sink, text)]
        # The key and the workspace go to the API in their own headers, and nowhere else.
        assert self.model.requests, "the scripted model was never called"
        for _url, headers, _body in self.model.requests:
            auth = headers.get("x-api-key") or headers.get("authorization", "").removeprefix("Bearer ")
            assert auth == KEY and headers.get("anthropic-workspace-id") == WORKSPACE
            assert self.token not in json.dumps(headers)
        # No child process gets a variable that is secret by name (names only: the values could be real).
        for program, env in self.children:
            named = sorted(name for name in env if is_secret_name(name))
            if named:
                problems.append(f"child process {program} got {named}")
        assert not problems, "secrets leaked:\n" + "\n".join(problems)


def _plain(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else str(value)


@pytest.fixture
def launch(tmp_path, monkeypatch):
    return Launch(tmp_path, monkeypatch)


# ----- the sweep -----

HELLO = 'print("Hello, World!")\n'
PRITN = 'pritn("Hello, World!")\n'
PROBE = (
    "import json\nimport os\n\n"
    "env = dict(os.environ)\n"
    "print(json.dumps(env, sort_keys=True))\n"
    'with open("env.json", "w", encoding="utf-8") as f:\n'
    "    json.dump(env, f)\n"
)


async def test_no_secret_leaks_end_to_end(launch):
    async with launch as run:
        sid = await run.session()
        voice = run.core.voice

        # Demo 1, by voice: push-to-talk, the transcript, then the task.
        voice.transcript = "Artemis, create a Python program that prints Hello World, and run it"
        run.model.steps += [
            tool("create_project", name="hello world"),
            tool("write_files", files=[{"path": "main.py", "content": HELLO}]),
            tool("run_program", entry="main.py", args=[]),
            answer("I created hello-world and ran main.py. It printed Hello, World!"),
        ]
        assert (await run.call("POST", "/voice/listen", json={"action": "start"})).status_code == 204
        assert (await run.call("POST", "/voice/listen", json={"action": "stop"})).status_code == 204
        await run.wait(lambda: any(e["type"] == "task.state" and e["data"]["state"] == "succeeded" for e in run.events))
        assert not run.model.steps
        # The voice hears the announcement a little after the task reports succeeded.
        await run.wait(lambda: "main.py ran successfully. It printed: Hello, World!" in run.spoken)

        # Demo 2: a failed run, explained, then fixed.
        await run.task(
            sid, "Create a Python program that calls pritn with Hello World, and run it.",
            tool("write_files", files=[{"path": "main.py", "content": PRITN}]),
            tool("run_program", entry="main.py", args=[]),
            answer("main.py calls pritn, which doesn't exist. It should be print."),
        )
        assert any(line.startswith("main.py failed with exit code 1. NameError") for line in run.spoken)
        await run.task(
            sid, "Fix it and run it again.",
            tool("edit_file", path="main.py", edits=[{"old": "pritn", "new": "print"}]),
            tool("run_program", entry="main.py", args=[]),
            answer("Fixed: it prints Hello, World! now."),
        )

        # Demo 3 and 3b: the blocked run is rejected; then the scan on request.
        tid = await run.task(
            sid, "Open the old-app project and run main.py.",
            tool("open_project", name="old-app"),
            tool("run_program", entry="main.py", args=[]),
            answer("I didn't run it: main.py passes typed text to the shell."),
        )
        assert any(text.startswith("Blocked: 1 high-severity issue") for text in run.said(tid))
        await run.task(
            sid, "Check this project for security vulnerabilities.",
            tool("scan_project", path=None),
            answer("main.py runs typed text in a shell."),
        )

        # Demo 4: the planted README is only read.
        await run.task(
            sid, "Open the calc project and summarize it.",
            tool("open_project", name="calc"),
            tool("read_file", path="README.md", start_line=None, end_line=None),
            answer("It adds numbers. The README also has instructions aimed at an AI, which I'm ignoring."),
        )

        # A program that shows its environment: printed, and written to a file it then reads back.
        await run.task(
            sid, "Write a program that prints its environment and saves it to env.json, and run it.",
            tool("create_project", name="env probe"),
            tool("write_files", files=[{"path": "probe.py", "content": PROBE}]),
            tool("run_program", entry="probe.py", args=[]),
            tool("read_file", path="env.json", start_line=None, end_line=None),
            answer("It printed its environment."),
        )
        (probe,) = [r for slug, entry, r in run.runs if entry == "probe.py"]
        for env in (json.loads(probe.stdout_tail), json.loads((run.projects / "env-probe" / "env.json").read_text())):
            assert not [name for name in env if is_secret_name(name)]
            assert not set(env.values()) & set(run.values.values())

        # The developer's own project holds the same secrets: the program prints them, where the
        # output gets cut, and crashes with them; Bandit quotes one; the edit's diff shows them.
        await run.task(
            sid, "Open keyed-app, turn on debug, and run show.py, crash.py and bigtail.py.",
            tool("open_project", name="keyed-app"),
            tool("read_file", path="settings.py", start_line=None, end_line=None),
            tool("edit_file", path="settings.py", edits=[{"old": "DEBUG = False", "new": "DEBUG = True"}]),
            tool("run_program", entry="show.py", args=[]),
            tool("run_program", entry="crash.py", args=[]),
            tool("run_program", entry="bigtail.py", args=[]),
            answer("Debug is on. show.py printed its settings, crash.py raised an error, bigtail.py printed a lot."),
        )
        raw = {entry: r for slug, entry, r in run.runs if slug == "keyed-app"}
        assert run.token in raw["show.py"].stdout_tail and KEY in raw["crash.py"].stderr_tail
        # The cut goes through the key; the runner drops the cut-off word, so no piece of the key is left.
        tail = raw["bigtail.py"].stdout_tail
        assert raw["bigtail.py"].truncated
        assert not any(KEY[i : i + PIECE] in tail for i in range(len(KEY) - PIECE + 1))

        # The API refuses: its error messages quote the key and the workspace.
        tid = await run.task(
            sid, "Say hello.", api_error(401, "authentication_error", f"invalid x-api-key: {KEY}"), expect="failed",
        )
        assert "The task failed. The Claude API key is missing or invalid. You can try again." in run.said(tid)
        await run.task(
            sid, "Say hello.",
            api_error(400, "invalid_request_error", f"key {KEY} is not scoped to workspace {WORKSPACE}"),
            expect="failed",
        )
        await run.task(
            sid, "Say hello.",
            api_error(500, "api_error", f"internal error for {KEY}"),
            api_error(500, "api_error", f"internal error for {KEY}"),
            expect="failed",
        )

        # Tracebacks in the core that carry the secrets: the agent crashes, and the voice fails once.
        real_request = ClaudeAgent._request

        async def crash(agent):
            ClaudeAgent._request = real_request
            raise ValueError(f"unexpected reply for key {KEY} in workspace {WORKSPACE} ({run.token})")

        run.mp.setattr(ClaudeAgent, "_request", crash)
        tid = await run.task(sid, "Say hello.", expect="failed")
        assert "The task failed. ValueError. You can try again." in run.said(tid)
        run.fail_next_announce = True
        assert (await run.call("POST", f"/sessions/{sid}/ready", json={"shortcut": "Control+Shift+Space"})).status_code == 204

        # Every other endpoint, and the ways a request can be refused.
        voice.transcript = "repeat"
        for action in ("start", "stop"):
            assert (await run.call("POST", "/voice/listen", json={"action": action})).status_code == 204
        await run.wait(lambda: any(e["type"] == "transcript.final" and e["data"]["text"] == "repeat" for e in run.events))
        for action in ("stop", "repeat"):
            assert (await run.call("POST", "/speech", json={"action": action})).status_code == 204
        diagnostic = {"code": "shortcut_registration_failed", "shortcut": "Control+Shift+Space"}
        assert (await run.call("POST", f"/sessions/{sid}/diagnostics", json=diagnostic)).status_code == 204
        assert (await run.call("GET", "/health")).status_code == 200
        assert (await run.call("GET", "/openapi.json")).status_code == 200
        assert (await run.call("GET", "/docs")).status_code == 200
        assert (await run.call("POST", f"/tasks/{tid}/reply")).status_code == 501
        assert (await run.call("POST", f"/tasks/{tid}/cancel")).status_code == 204
        resolved = next(e["data"] for e in run.events if e["type"] == "approval.requested")
        again = {"decision": "approve", "approval_hash": resolved["approval_hash"]}
        assert (await run.call("POST", f"/approvals/{resolved['approval_id']}", json=again)).status_code == 409
        assert (await run.call("POST", "/approvals/apr_000000000000", json=again)).status_code == 404
        assert (await run.call("GET", "/health", auth=False)).status_code == 401
        assert (await run.call("GET", "/health", auth=False, headers={"Authorization": "Bearer nope"})).status_code == 401
        assert (await run.call("GET", "/health", headers={"Origin": "https://evil.example"})).status_code == 403
        assert (await run.call("POST", f"/sessions/{sid}/tasks", json={"text": 1})).status_code == 422
        assert (await run.call("GET", "/sessions/ses_000000000000/events")).status_code == 404
        assert (await run.call("POST", "/speech", json={"action": "shout"})).status_code == 422

    # Everything above really happened, and was logged where the team looks.
    assert run.decisions and set(run.decisions) == {204}
    core_log = run.core_log()
    for line in ("agent crashed", "announce failed", "Claude API error 400", '"POST /sessions HTTP/1.1" 200'):
        assert line in core_log
    run.assert_no_leaks()


@pytest.mark.skipif(not browser_available(), reason="needs the echo group and a Chromium/Chrome/Edge")
async def test_no_secret_leaks_from_echo_check(launch):
    """Demo 5: the Playwright driver and the browser it starts get no secrets either (on Linux the
    environments of the running processes are read back from /proc)."""
    from artemis.echo import check

    async with launch as run:
        real_launch = check._launch

        async def launch_and_look(p):
            browser = await real_launch(p)
            if sys.platform.startswith("linux"):
                run.children += _descendants()
            return browser

        run.mp.setattr(check, "_launch", launch_and_look)
        sid = await run.session()
        before = (FIXTURE / "echo-page" / "before.html").read_text(encoding="utf-8")
        after = (FIXTURE / "echo-page" / "after.html").read_text(encoding="utf-8")
        await run.task(
            sid, "Open the signup-page project and check index.html for accessibility.",
            tool("open_project", name="signup-page"),
            tool("inspect_page", path="index.html"),
            answer("Seven problems; the worst is a button with no name."),
        )
        await run.task(
            sid, "Fix those problems in index.html, then check it again.",
            tool("edit_file", path="index.html", edits=[{"old": before, "new": after}]),
            tool("inspect_page", path="index.html"),
            answer("All fixed."),
        )
    assert any(text.startswith("Echo Check found") for text in run.spoken)
    assert any(text.startswith("Before: ") for text in run.spoken)
    assert any(program == "node" or "node" in program for program, _ in run.children)
    run.assert_no_leaks()


def _descendants() -> list[tuple[str, dict[str, str]]]:
    """(name, environment) of every running process under this one, from /proc."""
    children: dict[int, list[int]] = {}
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            ppid = int(stat.read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            continue
        children.setdefault(ppid, []).append(int(stat.parent.name))
    found, todo = [], [os.getpid()]
    while todo:
        for pid in children.get(todo.pop(), []):
            todo.append(pid)
            try:
                name = Path(f"/proc/{pid}/comm").read_text().strip()
                raw = Path(f"/proc/{pid}/environ").read_bytes()
            except OSError:
                continue
            env = dict(item.split("=", 1) for item in raw.decode("utf-8", "replace").split("\0") if "=" in item)
            found.append((f"proc:{name}", env))
    return found
