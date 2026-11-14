"""The FastAPI core (contracts.md §1, §6). 127.0.0.1 only, Bearer token on every route.

Run: ARTEMIS_TOKEN=... uv run uvicorn artemis.core.main:app --host 127.0.0.1 --port 8765
(infra/start.ps1 does this, with a fresh token and port.)
"""

import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.base import BaseHTTPMiddleware

from artemis.contracts import ApprovalResponse, TaskRequest

from .approvals import ApprovalNotFound, ApprovalStale
from .session import Session, TaskActive, new_id

log = logging.getLogger(__name__)

VERSION = "0.1.0"
ALLOWED_ORIGINS = ["http://tauri.localhost", "http://127.0.0.1:1420"]
PING_S = 15


class ListenBody(BaseModel):
    action: str  # "start" | "stop"


class SpeechBody(BaseModel):
    action: str  # "stop" | "repeat"


class Guard(BaseHTTPMiddleware):
    """§6: an unlisted Origin (including "null") gets 403; every request needs the Bearer token.

    CORS preflights are answered by CORSMiddleware outside this one, so they never reach it.
    """

    def __init__(self, app, token: str):
        super().__init__(app)
        self._expected = f"Bearer {token}".encode()

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")
        if origin is not None and origin not in ALLOWED_ORIGINS:
            return JSONResponse({"error": "origin_not_allowed"}, status_code=403)
        given = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(given, self._expected):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


class Core:
    """Everything the routes share. Built once per process by create_app."""

    def __init__(self, *, voice, make_runner, make_agent, projects_dir: Path, scan=None, log_dir=None, vocabulary=None):
        self.voice = voice
        self.make_runner = make_runner
        self.make_agent = make_agent
        self.projects_dir = Path(projects_dir)
        self.scan = scan
        self.log_dir = log_dir
        self.vocabulary = vocabulary  # project -> list[str], from agent/context.py when present
        self.sessions: dict[str, Session] = {}
        self.listen_lock = asyncio.Lock()
        self.watch_task: asyncio.Task | None = None

    def new_session(self) -> Session:
        sid = new_id("ses")
        session = Session(
            sid,
            voice=self.voice,
            runner=self.make_runner(self.projects_dir),
            agent=self.make_agent(),
            projects_dir=self.projects_dir,
            scan=self.scan,
            log_dir=self.log_dir,
            on_project=self._on_project,
        )
        self.sessions[sid] = session
        return session

    def _on_project(self, project) -> None:
        if self.vocabulary and self.voice:
            self.voice.set_vocabulary(self.vocabulary(project))

    def session(self, sid: str) -> Session:
        s = self.sessions.get(sid)
        if not s:
            raise HTTPException(404, {"error": "session_not_found"})
        return s

    def find_task(self, tid: str) -> Session:
        for s in self.sessions.values():
            if tid in s.tasks:
                return s
        raise HTTPException(404, {"error": "task_not_found"})

    def find_approval(self, aid: str) -> Session:
        for s in self.sessions.values():
            if s.approvals.has(aid):
                return s
        raise HTTPException(404, {"error": "approval_not_found"})

    def current(self) -> Session | None:
        """Voice belongs to the newest session (one per app launch)."""
        return next(reversed(self.sessions.values()), None)

    def voice_status(self) -> tuple[bool, str | None]:
        announcer = getattr(self.voice, "announcer", None)
        if announcer is None:
            return False, None
        return announcer.status()

    async def broadcast_voice_status(self, voice_ready: bool, screen_reader: str | None) -> None:
        for s in list(self.sessions.values()):
            await s.voice_status(voice_ready, screen_reader)


def create_app(core: Core, token: str, *, warm_voice: bool = True) -> FastAPI:
    if not token:
        raise RuntimeError("ARTEMIS_TOKEN is not set. Start Artemis with infra/start.ps1.")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        warm = getattr(core.voice, "warm", None)
        if warm_voice and warm:
            try:
                await asyncio.to_thread(warm)  # load the speech model now, not on the first request
            except Exception:
                log.exception("voice warm-up failed; push-to-talk will report the problem")
        announcer = getattr(core.voice, "announcer", None)
        if announcer is not None and hasattr(announcer, "watch"):
            core.watch_task = asyncio.create_task(announcer.watch(core.broadcast_voice_status))
        yield
        if core.watch_task:
            core.watch_task.cancel()
        for s in core.sessions.values():
            for t in s.tasks.values():
                if t.job and not t.job.done():
                    t.job.cancel()

    app = FastAPI(title="Artemis core", version=VERSION, lifespan=lifespan)
    app.state.core = core
    app.add_middleware(Guard, token=token)
    app.add_middleware(  # outermost: answers preflights for the two listed origins
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
    )

    @app.get("/health")
    async def health():
        voice_ready, screen_reader = core.voice_status()
        return {"ok": True, "version": VERSION, "voice_ready": voice_ready, "screen_reader": screen_reader}

    @app.post("/sessions")
    async def create_session():
        session = core.new_session()
        voice_ready, screen_reader = core.voice_status()
        await session.voice_status(voice_ready, screen_reader)  # §5.3: at session start
        return {"session_id": session.session_id}

    @app.get("/sessions/{sid}/events")
    async def events(sid: str, request: Request):
        session = core.session(sid)
        try:
            last = int(request.headers.get("last-event-id") or 0)
        except ValueError:
            last = 0
        queue = session.bus.subscribe()

        async def stream():
            try:
                seen = last
                for event in session.bus.since(last):  # replay, then live
                    seen = event.seq
                    yield {"id": str(event.seq), "data": event.model_dump_json()}
                while True:
                    event = await queue.get()
                    if event.seq <= seen:
                        continue
                    seen = event.seq
                    yield {"id": str(event.seq), "data": event.model_dump_json()}
            finally:
                session.bus.unsubscribe(queue)

        return EventSourceResponse(stream(), ping=PING_S)

    @app.post("/sessions/{sid}/tasks")
    async def create_task(sid: str, body: TaskRequest):
        session = core.session(sid)
        try:
            task = await session.submit(body)
        except TaskActive as exc:
            return JSONResponse({"error": "task_active", "task_id": exc.task_id}, status_code=409)
        return {"task_id": task.task_id}

    @app.post("/tasks/{tid}/reply")
    async def reply(tid: str):
        core.find_task(tid)
        return JSONResponse({"error": "not_supported"}, status_code=501)  # reserved, unused in the MVP

    @app.post("/tasks/{tid}/cancel", status_code=204)
    async def cancel(tid: str):
        session = core.find_task(tid)
        await session.cancel(tid)
        return Response(status_code=204)

    @app.post("/approvals/{aid}", status_code=204)
    async def decide(aid: str, body: ApprovalResponse):
        session = core.find_approval(aid)
        try:
            session.approvals.resolve(aid, body.decision, body.approval_hash)
        except ApprovalStale:
            return JSONResponse({"error": "stale"}, status_code=409)
        except ApprovalNotFound:
            raise HTTPException(404, {"error": "approval_not_found"})
        return Response(status_code=204)

    @app.post("/voice/listen", status_code=204)
    async def listen(body: ListenBody):
        session = core.current()
        if session is None:
            raise HTTPException(409, {"error": "no_session"})
        if body.action == "start":
            try:
                await core.voice.start_listening()  # §5.1: it stops speech and plays the earcon itself
            except Exception as exc:
                await session.error("mic_error", mic_message(exc), "Check the microphone, or type the request.")
        elif body.action == "stop":
            cid = new_id("cr")

            async def finish():
                try:
                    text = await core.voice.stop_listening(cid)
                except Exception as exc:
                    await session.error("mic_error", mic_message(exc), "Check the microphone, or type the request.")
                    return
                await session.spoken(text, cid)

            asyncio.create_task(finish())  # the transcript arrives as events
        else:
            raise HTTPException(422, {"error": "bad_action"})
        return Response(status_code=204)

    @app.post("/speech", status_code=204)
    async def speech(body: SpeechBody):
        if body.action == "stop":
            await core.voice.stop_speech()
        elif body.action == "repeat":
            await core.voice.repeat_last()
        else:
            raise HTTPException(422, {"error": "bad_action"})
        return Response(status_code=204)

    return app


def mic_message(exc: Exception) -> str:
    text = str(exc).strip()
    return text if text and len(text) < 160 else "The microphone didn't work."


