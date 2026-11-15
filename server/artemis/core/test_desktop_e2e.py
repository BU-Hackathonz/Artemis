"""Typing on the desktop, end to end over HTTP (contracts.md §9, §10).

The core is the real app with ClaudeAgent on a scripted model (artemis.test_no_secret_leaks's Launch
and ScriptedModel). The test plays the island: it says /ready with desktop=True, holds the SSE
stream, and answers each desktop.action over POST /desktop/{op_id}/result. It also plays the
developer, answering approvals with the hash the dialog carried.
"""

import asyncio
import json
import uuid

import sys

import pytest

# The desktop tools are offered and accepted only on Linux (contracts.md §9), where the island types.
pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="desktop tools are Linux-only")

pytest.importorskip("anthropic")

from artemis.test_no_secret_leaks import FINAL, Launch, answer, planned, tool  # noqa: E402

WINDOW = "org.gnome.TextEditor.desktop: notes.txt"
TERMINAL = "org.gnome.Ptyxis.desktop: ~/project"
TEXT = "hello"


@pytest.fixture
def launch(tmp_path, monkeypatch):
    return Launch(tmp_path, monkeypatch)


class Island:
    """The GNOME island and the developer, over HTTP. Answers each op as `window` says, and each
    approval as `decision` says."""

    def __init__(self, run: Launch, sid: str, window: str | None, decision: str = "approve"):
        self.run, self.sid, self.window, self.decision = run, sid, window, decision
        self.ops: list[dict] = []  # every desktop.action event it received, in full
        self.events: list[dict] = []
        self.approvals: list[dict] = []

    async def listen(self, last_id: int = 0) -> list[dict]:
        """Hold the stream; returns the events seen once cancelled."""
        headers = {**self.run.auth, "Last-Event-ID": str(last_id)}
        async with self.run.http.stream("GET", f"/sessions/{self.sid}/events", headers=headers) as response:
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:])
                self.events.append(event)
                if event["type"] == "desktop.action":
                    self.ops.append(event["data"])
                    self.run.spawn(self._answer(event["data"]))
                elif event["type"] == "approval.requested":
                    self.approvals.append(event["data"])
                    self.run.spawn(self._decide(event["data"]))
        return self.events

    async def _answer(self, op: dict) -> None:
        if op["kind"] == "focus":
            body = {"ok": True, "window": self.window}
        else:
            body = {"ok": True}
        r = await self.run.call("POST", f"/desktop/{op['op_id']}/result", json=body)
        assert r.status_code == 204

    async def _decide(self, approval: dict) -> None:
        body = {"decision": self.decision, "approval_hash": approval["approval_hash"]}
        assert (await self.run.call("POST", f"/approvals/{approval['approval_id']}", json=body)).status_code == 204


async def start(run: Launch, window: str | None, *, ready: bool = True, decision: str = "approve") -> Island:
    sid = (await run.call("POST", "/sessions")).json()["session_id"]
    island = Island(run, sid, window, decision)
    run.spawn(island.listen())
    await run.wait(lambda: island.events)
    if ready:
        assert (await run.call("POST", f"/sessions/{sid}/ready", json={"desktop": True})).status_code == 204
    return island


async def type_task(run: Launch, island: Island, *final) -> str:
    text = "type hello into my editor"
    run.model.steps.extend(planned(text, [tool("type_text", text=TEXT), *final]))
    body = {"client_request_id": f"cr_{uuid.uuid4().hex[:12]}", "source": "keyboard", "text": text}
    tid = (await run.call("POST", f"/sessions/{island.sid}/tasks", json=body)).json()["task_id"]
    await run.wait(lambda: any(
        e["type"] == "task.state" and e["task_id"] == tid and e["data"]["state"] in FINAL for e in island.events), 10)
    await run.wait(lambda: sum(e["type"] == "ring.cue" and e["data"]["action"] == "done" for e in island.events) >= 1, 5)
    assert not run.model.steps
    return tid


def types(island: Island, tid: str) -> list[str]:
    return [e["type"] for e in island.events if e["task_id"] == tid]


def final_state(island: Island, tid: str) -> str:
    return [e["data"]["state"] for e in island.events if e["type"] == "task.state" and e["task_id"] == tid][-1]


async def test_types_into_the_approved_window(launch):
    async with launch as run:
        island = await start(run, WINDOW)
        tid = await type_task(run, island, answer("Typed hello."))
        assert final_state(island, tid) == "succeeded"

        # focus first (no text, no window); focus again after the approval (it must not have moved);
        # then the type op with the approved window and text
        assert [(o["kind"], o["text"], o["window"]) for o in island.ops] == [
            ("focus", "", None), ("focus", "", None), ("type", TEXT, WINDOW)]
        # the approval shows the exact text and the window, and its hash was the one answered
        [approval] = island.approvals
        assert approval["kind"] == "type" and not approval["blocking"] and approval["findings"] == []
        assert approval["desktop"]["action"] == "type_text"
        assert approval["desktop"]["text"] == TEXT and approval["desktop"]["target"] == WINDOW
        assert TEXT in approval["summary"] and "notes.txt in TextEditor" in approval["summary"]

        # order: plan, focus op, approval, approved, type op, cue, done
        seq = types(island, tid)
        order = ["agent.plan", "desktop.action", "approval.requested", "approval.resolved", "desktop.action", "ring.cue"]
        at = -1
        for kind in order:
            at = seq.index(kind, at + 1)
        cues = [e["data"] for e in island.events if e["type"] == "ring.cue" and e["task_id"] == tid]
        assert [c["action"] for c in cues] == ["think", "type", "done"]  # the plan's think, the type, the done
        assert cues[1]["target"] == "notes.txt in TextEditor"
        assert seq.index("ring.cue", seq.index("approval.resolved")) > seq.index("approval.resolved")
        assert [e["data"]["action"] for e in island.events if e["type"] == "ring.cue"].count("done") == 1

        # the session log keeps the op, never the text
        log = (run.logs / f"{island.sid}.jsonl").read_text(encoding="utf-8")
        actions = [json.loads(line) for line in log.splitlines() if '"desktop.action"' in line]
        assert len(actions) == 3
        assert all(a["data"]["text"] in ("", "<redacted 0 chars>", f"<redacted {len(TEXT)} chars>") for a in actions)
        assert f"<redacted {len(TEXT)} chars>" in log
        assert f'"text":"{TEXT}"' not in log  # neither the op nor the approval event (desktop.text)
        assert "Type “hello”" not in log


async def test_terminal_needs_the_blocking_key(launch):
    async with launch as run:
        island = await start(run, TERMINAL, decision="reject")  # the client rejects a blocking approval
        tid = await type_task(run, island, answer("I did not type."))
        [approval] = island.approvals
        assert approval["blocking"] is True
        assert [f["rule"] for f in approval["findings"]] == ["desktop-terminal"]
        assert "terminal" in approval["summary"] and "Escape" in approval["summary"]
        assert approval["desktop"]["target"] == TERMINAL
        # rejected: only the focus op ever went out, nothing was typed, no type cue
        assert [o["kind"] for o in island.ops] == ["focus"]
        assert not [e for e in island.events if e["type"] == "ring.cue" and e["data"]["action"] == "type"]
        assert final_state(island, tid) == "succeeded"


async def test_no_island_no_approval(launch):
    async with launch as run:
        island = await start(run, WINDOW, ready=False)  # connected, but never said /ready
        tid = await type_task(run, island, answer("I couldn't type."))
        assert island.approvals == [] and island.ops == []
        assert "approval.requested" not in types(island, tid)
        assert "desktop.action" not in types(island, tid)
        # the model was told why
        last = json.loads(run.model.requests[-1][2])["messages"][-1]
        assert "desktop_unavailable" in json.dumps(last)

        # a ready island with nobody listening is just as unavailable
        await run.call("POST", f"/sessions/{island.sid}/ready", json={"desktop": True})
        ops = run.core.sessions[island.sid].desktop
        assert ops.available()  # ready and listening: the gate would now ask
        run.core.sessions[island.sid].desktop_ready = False
        assert not ops.available()


async def test_reconnect_does_not_resend_finished_ops(launch):
    async with launch as run:
        island = await start(run, WINDOW)
        tid = await type_task(run, island, answer("Typed hello."))
        assert final_state(island, tid) == "succeeded" and len(island.ops) == 3
        # a fresh island starts over from seq 0 after everything finished
        again = Island(run, island.sid, WINDOW)
        run.spawn(again.listen(0))
        total = len(run.core.sessions[island.sid].bus.history)
        await run.wait(lambda: again.events and again.events[-1]["seq"] >= total, 5)
        assert again.ops == []
        assert not any(e["type"] == "desktop.action" for e in again.events)
        assert any(e["type"] == "agent.plan" for e in again.events)  # the rest is replayed
