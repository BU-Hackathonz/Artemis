"""Desktop actions (contracts.md §9): request, resolve, timeout, cancel, the route and the log."""

import asyncio
import json

import httpx
import pytest

import artemis.core.desktop as desktop_module
from artemis.agent.stub import StubAgent
from artemis.contracts import DesktopResult
from artemis.core.app import Core, create_app
from artemis.core.bus import EventBus
from artemis.core.desktop import DesktopOps
from artemis.policy.testing import FakeRunner

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SECRET = "hunter2 the typed secret"


@pytest.fixture
def core(tmp_path):
    return Core(
        voice=None,
        make_runner=FakeRunner,
        make_agent=StubAgent,
        projects_dir=tmp_path / "projects",
        log_dir=tmp_path / "logs",
    )


@pytest.fixture
async def client(core):
    app = create_app(core, TOKEN, warm_voice=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
            yield c


def ready_session(core):
    """A session whose island said desktop=True and has an SSE subscriber."""
    session = core.new_session()
    session.desktop_ready = True
    session.bus.subscribe()
    return session


async def op_id_of(session):
    async def poll():
        while not any(e.type == "desktop.action" for e in session.bus.history):
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), 5)
    return next(e for e in session.bus.history if e.type == "desktop.action").data["op_id"]


async def test_success(core):
    session = ready_session(core)
    request = asyncio.create_task(session.desktop.request("type", SECRET, "tsk_1"))
    op_id = await op_id_of(session)
    event = next(e for e in session.bus.history if e.type == "desktop.action")
    assert op_id.startswith("op_") and len(op_id) == 27
    assert event.data == {"op_id": op_id, "kind": "type", "text": SECRET, "window": None}
    assert event.task_id == "tsk_1" and event.announce is None
    assert session.desktop.resolve(op_id, DesktopResult(ok=True))
    assert (await request) == DesktopResult(ok=True)


async def test_timeout_forgets_the_op(core, monkeypatch):
    monkeypatch.setattr(desktop_module, "BASE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(desktop_module, "PER_CHAR_S", 0.0)
    session = ready_session(core)
    result = await session.desktop.request("keys", "Control S", None)
    assert result == DesktopResult(ok=False, message="desktop_unavailable")
    op_id = next(e for e in session.bus.history if e.type == "desktop.action").data["op_id"]
    assert not session.desktop.resolve(op_id, DesktopResult(ok=True))  # a late result is ignored


async def test_unavailable_without_ready_or_subscriber(core):
    session = core.new_session()
    expected = DesktopResult(ok=False, message="desktop_unavailable")
    assert await session.desktop.request("type", "x", None) == expected  # nothing yet
    session.bus.subscribe()
    assert not session.desktop.available()  # subscriber but no /ready desktop=True
    assert await session.desktop.request("type", "x", None) == expected
    other = core.new_session()
    other.desktop_ready = True
    assert not other.desktop.available()  # ready but no subscriber
    assert await other.desktop.request("type", "x", None) == expected
    assert not any(e.type == "desktop.action" for s in (session, other) for e in s.bus.history)


async def test_resolve_is_single_use(core):
    session = ready_session(core)
    request = asyncio.create_task(session.desktop.request("type", "a", None))
    op_id = await op_id_of(session)
    assert not session.desktop.resolve("op_unknown", DesktopResult(ok=True))
    assert session.desktop.resolve(op_id, DesktopResult(ok=False, message="focus moved"))
    assert not session.desktop.resolve(op_id, DesktopResult(ok=True))
    assert (await request) == DesktopResult(ok=False, message="focus moved")


async def test_cancel_all(core):
    session = ready_session(core)
    request = asyncio.create_task(session.desktop.request("type", "a", None))
    op_id = await op_id_of(session)
    session.desktop.cancel_all()
    assert (await request) == DesktopResult(ok=False, message="canceled")
    assert not session.desktop.resolve(op_id, DesktopResult(ok=True))


async def test_ready_route_sets_desktop_flag(client, core):
    sid = (await client.post("/sessions", headers=AUTH)).json()["session_id"]
    session = core.sessions[sid]
    assert session.desktop_ready is False
    assert (await client.post(f"/sessions/{sid}/ready", headers=AUTH, json={})).status_code == 204
    assert session.desktop_ready is False  # an older client that omits the field
    await client.post(f"/sessions/{sid}/ready", headers=AUTH, json={"desktop": True})
    assert session.desktop_ready is True


async def test_result_route(client, core):
    session = ready_session(core)
    request = asyncio.create_task(session.desktop.request("type", "a", None))
    op_id = await op_id_of(session)
    r = await client.post(f"/desktop/{op_id}/result", headers=AUTH, json={"ok": True})
    assert r.status_code == 204
    assert (await request).ok
    again = await client.post(f"/desktop/{op_id}/result", headers=AUTH, json={"ok": True})
    assert again.status_code == 404 and again.json()["detail"] == {"error": "desktop_op_not_found"}
    unknown = await client.post("/desktop/op_nope/result", headers=AUTH, json={"ok": True})
    assert unknown.status_code == 404


async def test_result_route_needs_the_token(client):
    r = await client.post("/desktop/op_x/result", json={"ok": True})
    assert r.status_code == 401


async def test_task_cancel_cancels_pending_ops(core):
    from artemis.contracts import TaskRequest
    from artemis.core.session import Task

    session = ready_session(core)
    task = Task("tsk_1", TaskRequest(client_request_id="cr_1", source="keyboard", text="type it"))
    task.job = asyncio.create_task(asyncio.sleep(60))
    session.tasks["tsk_1"] = task
    request = asyncio.create_task(session.desktop.request("type", "a", "tsk_1"))
    await op_id_of(session)
    assert await session.cancel("tsk_1")
    assert (await request).message == "canceled"


async def test_lifespan_end_cancels_pending_ops(core):
    app = create_app(core, TOKEN, warm_voice=False)
    session = ready_session(core)
    async with app.router.lifespan_context(app):
        request = asyncio.create_task(session.desktop.request("type", "a", None))
        await op_id_of(session)
    assert (await request).message == "canceled"


async def test_log_redacts_text_but_sse_event_keeps_it(tmp_path):
    path = tmp_path / "log.jsonl"
    bus = EventBus("ses_1", log_path=path)
    queue = bus.subscribe()
    assert bus.subscriber_count == 1
    ops = DesktopOps(bus.emit, ready=lambda: True, subscribers=lambda: bus.subscriber_count)
    request = asyncio.create_task(ops.request("type", SECRET, None))
    event = await asyncio.wait_for(queue.get(), 5)
    assert event.data["text"] == SECRET  # SSE carries the real text
    line = json.loads(path.read_text().splitlines()[-1])
    assert line["data"]["text"] == f"<redacted {len(SECRET)} chars>"
    assert SECRET not in path.read_text()
    ops.cancel_all()
    await request
    bus.unsubscribe(queue)
    assert bus.subscriber_count == 0


async def test_focus_asks_for_the_window_and_type_carries_it(core):
    session = ready_session(core)
    ask = asyncio.create_task(session.desktop.request("focus", task_id="tsk_1"))
    op = await op_id_of(session)
    event = next(e for e in session.bus.history if e.type == "desktop.action")
    assert event.data["kind"] == "focus" and event.data["text"] == "" and event.data["window"] is None
    assert session.desktop.resolve(op, DesktopResult(ok=True, window="org.gnome.TextEditor.desktop: notes.txt"))
    assert (await ask).window == "org.gnome.TextEditor.desktop: notes.txt"

    session.bus.history.clear()
    typing = asyncio.create_task(
        session.desktop.request("type", "hello", "tsk_1", window="org.gnome.TextEditor.desktop: notes.txt")
    )
    op = await op_id_of(session)
    event = next(e for e in session.bus.history if e.type == "desktop.action")
    assert event.data["window"] == "org.gnome.TextEditor.desktop: notes.txt"
    session.desktop.resolve(op, DesktopResult(ok=True))
    assert (await typing).ok


async def test_a_finished_action_is_stale_so_the_stream_never_sends_it_again(core):
    session = ready_session(core)
    request = asyncio.create_task(session.desktop.request("type", "hello", "tsk_1"))
    op = await op_id_of(session)
    event = next(e for e in session.bus.history if e.type == "desktop.action")
    assert not session.desktop.is_stale(event)  # pending: an island that connects now still gets it
    session.desktop.resolve(op, DesktopResult(ok=True))
    await request
    assert session.desktop.is_stale(event)  # answered: a reconnect replay must not type it twice
    other = event.model_copy(update={"type": "agent.message"})
    assert not session.desktop.is_stale(other)  # only desktop.action is ever skipped


async def test_a_result_that_lands_as_the_timer_fires_is_not_lost(core, monkeypatch):
    session = ready_session(core)
    real = asyncio.wait_for

    async def fires_after_the_answer(awaitable, timeout):
        op_id = next(iter(session.desktop._pending))
        session.desktop.resolve(op_id, DesktopResult(ok=True))  # the island answered...
        raise asyncio.TimeoutError  # ...in the turn the timer fired

    monkeypatch.setattr(desktop_module.asyncio, "wait_for", fires_after_the_answer)
    result = await session.desktop.request("type", "a", None)
    monkeypatch.setattr(desktop_module.asyncio, "wait_for", real)
    assert result == DesktopResult(ok=True)


async def test_closing_the_last_stream_ends_waiting_ops_at_once(core):
    session = core.new_session()
    session.desktop_ready = True
    queue = session.bus.subscribe()
    request = asyncio.create_task(session.desktop.request("type", "a", None))
    await op_id_of(session)
    session.desktop.subscribers_changed()
    assert not request.done()  # a stream is still open
    session.bus.unsubscribe(queue)
    session.desktop.subscribers_changed()
    assert await asyncio.wait_for(request, 1) == DesktopResult(ok=False, message="desktop_unavailable")


async def test_typed_text_leaves_the_replay_history_once_the_op_is_over(core):
    session = ready_session(core)
    request = asyncio.create_task(session.desktop.request("type", SECRET, None))
    op = await op_id_of(session)
    assert SECRET in session.bus.history[-1].data["text"]  # pending: a reconnecting island needs it
    session.desktop.resolve(op, DesktopResult(ok=True))
    await request
    assert all(SECRET not in e.model_dump_json() for e in session.bus.history)


async def test_log_redacts_the_typing_approval(tmp_path):
    from artemis.contracts import EventDraft

    path = tmp_path / "log.jsonl"
    bus = EventBus("ses_1", log_path=path)
    from artemis.contracts import Announce

    data = {"approval_id": "apr_1", "kind": "type", "approval_hash": "h", "summary": f"Type “{SECRET}” into Editor.",
            "desktop": {"action": "type_text", "target": "w", "text": SECRET}}
    await bus.emit(EventDraft(type="approval.requested", data=data,
                              announce=Announce(text=f"Type “{SECRET}”", priority="high")))
    assert SECRET not in path.read_text()
    assert bus.history[-1].data["desktop"]["text"] == SECRET  # SSE and the approval dialog still carry it
