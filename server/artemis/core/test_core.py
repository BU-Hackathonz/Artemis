"""End to end through HTTP: StubAgent + FakeRunner + a fake voice, the Phase 1 walking skeleton."""

import asyncio
import json

import httpx
import pytest

from artemis.agent.stub import StubAgent
from artemis.core.app import Core, create_app
from artemis.core.approvals import ApprovalStale, SessionApprovals
from artemis.core.bus import EventBus
from artemis.core.session import normalize
from artemis.contracts import Approval, Decision, EventDraft
from artemis.policy.testing import FakeRunner

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeAnnouncer:
    def __init__(self):
        self.state = (True, "nvda")

    def status(self):
        return self.state

    async def watch(self, callback, interval_s=2.0):
        await callback(*self.state)
        await asyncio.Event().wait()


class FakeVoice:
    def __init__(self, transcript="create a Python program that prints Hello World"):
        self.transcript = transcript
        self.spoken: list[tuple[str, str]] = []
        self.calls: list[str] = []
        self.announcer = FakeAnnouncer()
        self.vocabulary: list[str] = []

    async def start_listening(self):
        self.calls.append("start")

    async def stop_listening(self, client_request_id):
        self.calls.append("stop")
        return self.transcript

    def set_vocabulary(self, words):
        self.vocabulary = words

    async def announce(self, text, priority):
        self.spoken.append((priority, text))

    async def stop_speech(self):
        self.calls.append("stop_speech")

    async def repeat_last(self):
        self.calls.append("repeat")


@pytest.fixture
def voice():
    return FakeVoice()


@pytest.fixture
def core(tmp_path, voice):
    return Core(
        voice=voice,
        make_runner=FakeRunner,
        make_agent=StubAgent,
        projects_dir=tmp_path / "projects",
        log_dir=tmp_path / "logs",
    )


@pytest.fixture
async def client(core):
    app = create_app(core, TOKEN, warm_voice=False)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            yield c


async def wait_for(session, predicate, timeout=5.0):
    async def poll():
        while not predicate(session):
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


def types(session):
    return [e.type for e in session.bus.history]


def pending(session) -> Approval | None:
    approvals = session.approvals.pending()
    return approvals[0] if approvals else None


async def start(client, core):
    sid = (await client.post("/sessions", headers=AUTH)).json()["session_id"]
    return sid, core.sessions[sid]


# ----- guard (§6) -----


async def test_token_required_everywhere(client):
    assert (await client.get("/health")).status_code == 401
    assert (await client.get("/health", headers={"Authorization": "Bearer wrong"})).status_code == 401
    ok = await client.get("/health", headers=AUTH)
    assert ok.status_code == 200 and ok.json() == {
        "ok": True, "version": "0.1.0", "voice_ready": True, "screen_reader": "nvda"
    }


@pytest.mark.parametrize("origin,status", [
    ("http://tauri.localhost", 200),
    ("http://127.0.0.1:1420", 200),
    ("https://evil.example", 403),
    ("null", 403),
])
async def test_origin_check(client, origin, status):
    response = await client.get("/health", headers={**AUTH, "Origin": origin})
    assert response.status_code == status


async def test_cors_preflight_for_listed_origin_only(client):
    ok = await client.options("/sessions", headers={
        "Origin": "http://tauri.localhost",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    })
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "http://tauri.localhost"
    bad = await client.options("/sessions", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
    })
    assert "access-control-allow-origin" not in bad.headers


def test_refuses_to_start_without_token(core):
    with pytest.raises(RuntimeError):
        create_app(core, "")


# ----- the walking skeleton -----


async def test_hello_world_approve_twice(client, core, voice):
    sid, session = await start(client, core)
    assert session.bus.history[0].type == "voice.status"
    r = await client.post(f"/sessions/{sid}/tasks", headers=AUTH, json={
        "client_request_id": "cr_1", "source": "keyboard", "text": "create a Python program that prints Hello World"})
    tid = r.json()["task_id"]

    for kind in ("write_files", "run_program"):
        await wait_for(session, lambda s: pending(s) is not None)
        approval = pending(session)
        assert approval.kind == kind
        r = await client.post(f"/approvals/{approval.approval_id}", headers=AUTH,
                              json={"decision": "approve", "approval_hash": approval.approval_hash})
        assert r.status_code == 204
        # deciding twice is stale
        again = await client.post(f"/approvals/{approval.approval_id}", headers=AUTH,
                                  json={"decision": "approve", "approval_hash": approval.approval_hash})
        assert again.status_code == 409

    await wait_for(session, lambda s: not s.tasks[tid].active)
    assert session.tasks[tid].state == "succeeded"
    t = types(session)
    assert t[:2] == ["voice.status", "task.state"]
    assert "file.changed" in t and "run.result" in t and t[-1] == "task.state"
    seqs = [e.seq for e in session.bus.history]
    assert seqs == list(range(1, len(seqs) + 1))
    assert any("Hello, World" in text for _, text in voice.spoken)
    log_lines = (core.log_dir / f"{sid}.jsonl").read_text().splitlines()
    assert len(log_lines) == len(seqs) and json.loads(log_lines[-1])["seq"] == seqs[-1]


async def test_reject_writes_nothing(client, core):
    sid, session = await start(client, core)
    r = await client.post(f"/sessions/{sid}/tasks", headers=AUTH,
                          json={"client_request_id": "cr_1", "source": "keyboard", "text": "hello"})
    tid = r.json()["task_id"]
    await wait_for(session, lambda s: pending(s) is not None)
    a = pending(session)
    bad = await client.post(f"/approvals/{a.approval_id}", headers=AUTH,
                            json={"decision": "approve", "approval_hash": "sha256:wrong"})
    assert bad.status_code == 409
    await client.post(f"/approvals/{a.approval_id}", headers=AUTH,
                      json={"decision": "reject", "approval_hash": a.approval_hash})
    await wait_for(session, lambda s: not s.tasks[tid].active)
    assert session.tasks[tid].state == "succeeded"
    assert "file.changed" not in types(session)
    assert not (core.projects_dir / "hello-world").exists()


async def test_one_active_task_and_idempotency(client, core):
    sid, session = await start(client, core)
    body = {"client_request_id": "cr_1", "source": "keyboard", "text": "hello"}
    first = await client.post(f"/sessions/{sid}/tasks", headers=AUTH, json=body)
    same = await client.post(f"/sessions/{sid}/tasks", headers=AUTH, json=body)
    assert same.json() == first.json()
    other = await client.post(f"/sessions/{sid}/tasks", headers=AUTH, json={**body, "client_request_id": "cr_2"})
    assert other.status_code == 409 and other.json() == {"error": "task_active", "task_id": first.json()["task_id"]}


async def test_cancel_while_awaiting_approval(client, core):
    sid, session = await start(client, core)
    tid = (await client.post(f"/sessions/{sid}/tasks", headers=AUTH,
                             json={"client_request_id": "cr_1", "source": "keyboard", "text": "hello"})).json()["task_id"]
    await wait_for(session, lambda s: pending(s) is not None)
    assert (await client.post(f"/tasks/{tid}/cancel", headers=AUTH)).status_code == 204
    await wait_for(session, lambda s: not s.tasks[tid].active)
    assert session.tasks[tid].state == "canceled"
    resolved = [e for e in session.bus.history if e.type == "approval.resolved"]
    assert resolved and resolved[-1].data["by"] == "invalidated"
    assert session.approvals.pending() == []


async def test_agent_crash_is_spoken_not_silent(client, core, voice):
    class Boom:
        async def run(self, task, tools, emit):
            raise RuntimeError("secret detail")

    core.make_agent = Boom
    sid, session = await start(client, core)
    tid = (await client.post(f"/sessions/{sid}/tasks", headers=AUTH,
                             json={"client_request_id": "cr_1", "source": "keyboard", "text": "x"})).json()["task_id"]
    await wait_for(session, lambda s: not s.tasks[tid].active)
    assert session.tasks[tid].state == "failed"
    errors = [e for e in session.bus.history if e.type == "error"]
    assert errors and errors[-1].announce.priority == "high"
    assert "secret detail" not in json.dumps([e.model_dump(mode="json") for e in session.bus.history])


# ----- voice (§5) -----


async def test_push_to_talk_creates_a_task(client, core, voice):
    sid, session = await start(client, core)
    assert (await client.post("/voice/listen", headers=AUTH, json={"action": "start"})).status_code == 204
    assert (await client.post("/voice/listen", headers=AUTH, json={"action": "stop"})).status_code == 204
    await wait_for(session, lambda s: s.tasks)
    assert voice.calls[:2] == ["start", "stop"]
    final = [e for e in session.bus.history if e.type == "transcript.final"][0]
    assert final.data["text"].startswith("create a Python program")
    assert next(iter(session.tasks.values())).request.source == "voice"


async def test_spoken_while_active_and_control_words(client, core, voice):
    sid, session = await start(client, core)
    await client.post(f"/sessions/{sid}/tasks", headers=AUTH,
                      json={"client_request_id": "cr_1", "source": "keyboard", "text": "hello"})
    await wait_for(session, lambda s: pending(s) is not None)

    voice.transcript = "Yes."
    await client.post("/voice/listen", headers=AUTH, json={"action": "stop"})
    await wait_for(session, lambda s: any("Use the keyboard" in (e.announce.text if e.announce else "") for e in s.bus.history))
    assert pending(session) is not None, "a spoken yes must never decide an approval"

    voice.transcript = "make another one"
    await client.post("/voice/listen", headers=AUTH, json={"action": "stop"})
    await wait_for(session, lambda s: any(e.type == "error" and e.data["code"] == "task_active" for e in s.bus.history))
    assert len(session.tasks) == 1

    voice.transcript = "Never mind!"
    await client.post("/voice/listen", headers=AUTH, json={"action": "stop"})
    await wait_for(session, lambda s: not s.active_task)
    assert next(iter(session.tasks.values())).state == "canceled"

    voice.transcript = "cancel"
    await client.post("/voice/listen", headers=AUTH, json={"action": "stop"})
    await wait_for(session, lambda s: any(e.announce and e.announce.text == "Nothing to cancel." for e in s.bus.history))

    voice.transcript = "say that again"
    await client.post("/voice/listen", headers=AUTH, json={"action": "stop"})
    await wait_for(session, lambda s: "repeat" in voice.calls)


async def test_speech_controls(client, voice):
    await client.post("/speech", headers=AUTH, json={"action": "stop"})
    await client.post("/speech", headers=AUTH, json={"action": "repeat"})
    assert voice.calls == ["stop_speech", "repeat"]


def test_normalize():
    assert normalize("Cancel that!") == "cancel that"
    assert normalize("  NEVER   mind. ") == "never mind"


# ----- SSE replay -----


async def test_sse_replays_from_last_event_id(core, tmp_path):
    """Over a real socket: httpx's in-process transport buffers whole responses, so no SSE there."""
    import socket

    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(create_app(core, TOKEN, warm_voice=False), host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.02)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            sid, session = await start(client, core)
            for i in range(3):
                await session.say(None, f"m{i}")
            seen = []
            async with client.stream("GET", f"/sessions/{sid}/events", headers={**AUTH, "Last-Event-ID": "2"}) as r:
                assert r.headers["content-type"].startswith("text/event-stream")
                await session.say(None, "live")
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        seen.append(json.loads(line[5:])["seq"])
                        if len(seen) == 3:
                            break
            assert seen == [3, 4, 5]
            unauthorized = await client.get(f"/sessions/{sid}/events")
            assert unauthorized.status_code == 401
    finally:
        server.should_exit = True
        await serving


# ----- units -----


async def test_bus_adds_envelope_and_speaks():
    spoken = []

    async def speak(text, priority):
        spoken.append(text)

    bus = EventBus("ses_1", speak=speak)
    q = bus.subscribe()
    await bus.emit(EventDraft(type="agent.message", announce={"text": "hi", "priority": "normal"},
                              data={"text": "hi", "kind": "answer"}))
    event = q.get_nowait()
    assert (event.seq, event.session_id) == (1, "ses_1") and spoken == ["hi"]


async def test_approvals_hash_must_match():
    approvals = SessionApprovals()
    a = Approval(approval_id="apr_1", kind="write_files", approval_hash="sha256:x", summary="s")
    waiter = asyncio.create_task(approvals.request("tsk_1", a))
    await asyncio.sleep(0)
    with pytest.raises(ApprovalStale):
        approvals.resolve("apr_1", "approve", "sha256:y")
    approvals.resolve("apr_1", "approve", "sha256:x")
    assert await waiter == Decision.approve
    with pytest.raises(ApprovalStale):
        approvals.resolve("apr_1", "approve", "sha256:x")
