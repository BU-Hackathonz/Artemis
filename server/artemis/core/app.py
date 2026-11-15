"""The FastAPI core (contracts.md §1, §6). 127.0.0.1 only, Bearer token on every route.

Run: ARTEMIS_TOKEN=... uv run uvicorn artemis.core.main:app --host 127.0.0.1 --port 8765
(infra/start.ps1 does this, with a fresh token and port.)
"""

import asyncio
import hmac
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.base import BaseHTTPMiddleware

from artemis.contracts import (
    ApprovalResponse,
    ClientDiagnostic,
    ClientReady,
    DesktopResult,
    TaskRequest,
    VoiceSettingsPatch,
)
from artemis.runner.local import NoInterpreter
from artemis.voice import profile as voice_profile
from artemis.voice.speech import orca_running

from . import settings as voice_settings
from .approvals import ApprovalNotFound, ApprovalStale
from .session import HEARTBEAT_S, Session, TaskActive, new_id

log = logging.getLogger(__name__)

VERSION = "0.1.0"
ALLOWED_ORIGINS = ["http://tauri.localhost", "http://127.0.0.1:1420"]
PING_S = 15
NO_INTERPRETER_RECOVERY = "Until then I can write files but not run them."


class ListenBody(BaseModel):
    action: str  # "start" | "stop"


class SpeechBody(BaseModel):
    action: str  # "stop" | "repeat" | "sample"


SAMPLE_TEXT = "This is how I sound."  # the voice picker's Test voice: fixed, never free text


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

    def __init__(
        self,
        *,
        voice,
        make_runner,
        make_agent,
        projects_dir: Path,
        scan=None,
        log_dir=None,
        vocabulary=None,
        heartbeat_s: float = HEARTBEAT_S,
        prefs_path: Path | None = None,
    ):
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
        self.activity_task: asyncio.Task | None = None
        self.wake = None  # the "Hey Artemis" listener, when ARTEMIS_LISTEN_MODE=wake
        self.activity: tuple[str, str | None] = ("idle", None)  # the last voice.activity sent
        self.heartbeat_s = heartbeat_s
        # Where the answer style is remembered (conversation mode only). None, the default, remembers
        # nothing: only main.py passes a path, so tests never touch the home folder.
        self.prefs_path = prefs_path
        # Interpreter pre-check: probed once per process, then reused by every session.
        self._interpreter_probe: asyncio.Task | None = None
        self.background: set[asyncio.Task] = set()  # per-session checks, canceled at shutdown
        self.wire_voice()

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
            heartbeat_s=self.heartbeat_s,
            prefs_path=self.prefs_path,
        )
        self.sessions[sid] = session
        return session

    async def check_interpreter(self, session: Session) -> None:
        """Risk 7: say up front when there is no Python for user programs, instead of the
        gateway's generic "Something went wrong with run_program" at the first run.

        Only runners with a lazy `python` property (LocalRunner) are checked; FakeRunner has none.
        Looked up on the class, because touching the property is the probe itself.
        """
        if not hasattr(type(session.runner), "python"):
            return
        if self._interpreter_probe is None:
            self._interpreter_probe = asyncio.create_task(_probe_python(session.runner))
        problem = await asyncio.shield(self._interpreter_probe)
        if problem:
            await session.error("no_interpreter", problem, NO_INTERPRETER_RECOVERY)

    def spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.background.add(task)
        task.add_done_callback(self.background.discard)

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

    def find_desktop_op(self, op_id: str) -> Session:
        for s in self.sessions.values():
            if s.desktop.has(op_id):
                return s
        raise HTTPException(404, {"error": "desktop_op_not_found"})

    def current(self) -> Session | None:
        """Voice belongs to the newest session (one per app launch)."""
        return next(reversed(self.sessions.values()), None)

    def voice_status(self) -> tuple[bool, str | None]:
        announcer = getattr(self.voice, "announcer", None)
        if announcer is None:
            return False, None
        voice_ready, screen_reader = announcer.status()
        # Orca runs beside Artemis's own Linux voice, so the core reports it (contracts.md §5.3).
        if sys.platform.startswith("linux") and orca_running():
            screen_reader = "orca"
        return voice_ready, screen_reader

    async def watch_voice_status(self, interval_s: float = 2.0) -> None:
        """Emit voice.status at start and on every change of voice_status(), Orca included.

        The announcer's own watch cannot see Orca start or stop, so the core polls voice_status().
        """
        last = None
        while True:
            now = self.voice_status()
            if now != last:
                last = now
                try:
                    await self.broadcast_voice_status(*now)
                except Exception:  # one failed emit must not end status updates for the session
                    log.exception("voice status broadcast failed")
            await asyncio.sleep(interval_s)

    def active_engine(self) -> str:
        """The speaker that would say the next line, or "none" when the voice has no announcer."""
        method = getattr(getattr(self.voice, "announcer", None), "active_engine", None)
        return method() if method is not None else "none"

    def voice_state(self) -> tuple[bool, str | None, str]:
        voice_ready, screen_reader = self.voice_status()
        return voice_ready, screen_reader, self.active_engine()

    async def broadcast_voice_status(self, voice_ready: bool, screen_reader: str | None) -> None:
        engine = self.active_engine()
        for s in list(self.sessions.values()):
            await s.voice_status(voice_ready, screen_reader, engine)

    def voice_activity(self) -> tuple[str, str | None]:
        """(state, engine) from the voice's real state: the mic (listening, from the press until it
        closes), Whisper (transcribing), then audible speech (speaking). Never inferred from timers."""
        voice = self.voice
        if getattr(voice, "busy_listening", getattr(voice, "listening", False)):
            return "listening", None
        if self.wake is not None and self.wake.armed:  # heard "Hey Artemis": waiting for the request
            return "listening", None
        if getattr(voice, "transcribing", False):
            return "transcribing", None
        announcer = getattr(voice, "announcer", None)
        method = getattr(announcer, "speaking_engine", None)
        engine = method() if method is not None else None
        return ("speaking", engine) if engine else ("idle", None)

    async def watch_activity(self, interval_s: float = 0.1) -> None:
        """Send voice.activity to every session when the state changes (contracts.md section 2)."""
        while True:
            try:
                now = self.voice_activity()
                if now != self.activity:
                    self.activity = now
                    for s in list(self.sessions.values()):
                        await s.voice_activity(*now)
            except Exception:  # a voice that can't be asked must not end the updates
                log.exception("voice activity check failed")
            await asyncio.sleep(interval_s)

    def wake_request(self, text: str) -> None:
        """A request heard after "Hey Artemis": handled exactly like a push-to-talk transcript."""
        session = self.current()
        if session is None:
            log.warning("Wake word: a request was heard before any session; ignoring it")
            return
        self.spawn(session.spoken(text, new_id("cr")))

    def start_wake(self) -> None:
        """Start hands-free listening if ARTEMIS_LISTEN_MODE=wake and the voice can transcribe."""
        if not hasattr(self.voice, "_transcribe"):
            log.warning("ARTEMIS_LISTEN_MODE=wake needs the Whisper voice; push-to-talk only")
            return
        from artemis.voice.wake import WakeListener  # numpy and the mic only when asked for

        listener = WakeListener(self.voice, self.wake_request)
        try:
            listener.start()
        except Exception as e:  # no device, busy, permission: push-to-talk still works
            log.warning("Wake word could not start the microphone (%s); push-to-talk only", type(e).__name__)
            return
        self.wake = listener

    def wire_voice(self) -> None:
        """Say the ElevenLabs fallback notice through the bus when that voice gives up. Called at
        startup and again after every apply(), since apply() can build a new ElevenLabsSpeaker."""
        eleven = getattr(getattr(self.voice, "announcer", None), "eleven", None)
        if eleven is not None and hasattr(eleven, "on_give_up"):
            eleven.on_give_up = self._say_fallback_notice

    def _say_fallback_notice(self, text: str) -> None:
        # Called on the event-loop thread (ElevenLabsSpeaker hands it over with call_soon_threadsafe).
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            log.warning("fallback notice not spoken: no event loop")
            return
        session = self.current()
        if session is None:
            log.warning("fallback notice not spoken: no session yet")
            return
        self.spawn(session.say(None, text))  # an agent.message at normal priority, spoken once

    def apply_voice_profile(self, profile: voice_profile.VoiceProfile) -> None:
        """Switch the voice to `profile` from the next line on, then re-wire the fallback notice."""
        announcer = getattr(self.voice, "announcer", None)
        if announcer is None or not hasattr(announcer, "apply"):
            return
        announcer.apply(profile)
        self.wire_voice()


def create_app(core: Core, token: str, *, warm_voice: bool = True) -> FastAPI:
    if not token:
        raise RuntimeError("ARTEMIS_TOKEN is not set. Start Artemis with infra/start.ps1.")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:  # the saved voice profile, with the environment on top, so it holds across restarts
            core.apply_voice_profile(voice_profile.load())
        except Exception:
            log.exception("voice profile could not be applied; using the environment only")
        warm = getattr(core.voice, "warm", None)
        if warm_voice and warm:
            try:
                await asyncio.to_thread(warm)  # load the speech model now, not on the first request
            except Exception:
                log.exception("voice warm-up failed; push-to-talk will report the problem")
        announcer = getattr(core.voice, "announcer", None)
        if announcer is not None:
            core.watch_task = asyncio.create_task(core.watch_voice_status())
        core.activity_task = asyncio.create_task(core.watch_activity())
        if os.environ.get("ARTEMIS_LISTEN_MODE", "").strip().lower() == "wake":
            core.start_wake()
        yield
        if core.wake is not None:
            core.wake.stop()
        if core.watch_task:
            core.watch_task.cancel()
        if core.activity_task:
            core.activity_task.cancel()
        for task in [*core.background, core._interpreter_probe]:
            if task and not task.done():
                task.cancel()
        for s in core.sessions.values():
            s.desktop.cancel_all()  # the session is over: nothing waits for the island
            for t in s.tasks.values():
                if t.job and not t.job.done():
                    t.job.cancel()
                if t.heartbeat and not t.heartbeat.done():
                    t.heartbeat.cancel()

    app = FastAPI(title="Artemis core", version=VERSION, lifespan=lifespan)
    app.state.core = core
    app.add_middleware(Guard, token=token)
    app.add_middleware(  # outermost: answers preflights for the two listed origins
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["GET", "POST", "PATCH"],  # PATCH: /settings/voice only
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
        await session.voice_status(voice_ready, screen_reader, core.active_engine())  # §5.3: at session start
        if core.activity[0] != "idle":  # a session starts idle; tell it if the voice is busy right now
            await session.voice_activity(*core.activity)
        # In the background, so a slow probe never delays the client; the error event (if any)
        # still lands after voice.status and is replayed to a late SSE subscriber.
        core.spawn(core.check_interpreter(session))
        return {"session_id": session.session_id}

    @app.get("/sessions/{sid}/events")
    async def events(sid: str, request: Request):
        session = core.session(sid)
        try:
            last = int(request.headers.get("last-event-id") or 0)
        except ValueError:
            last = 0
        queue = session.bus.subscribe()

        stale = session.desktop.is_stale  # a finished desktop.action is never sent again, live or replayed

        async def stream():
            try:
                seen = last
                for event in session.bus.since(last):  # replay, then live
                    seen = event.seq
                    if stale(event):
                        continue
                    yield {"id": str(event.seq), "data": event.model_dump_json()}
                while True:
                    event = await queue.get()
                    if event.seq <= seen:
                        continue
                    seen = event.seq
                    if stale(event):
                        continue
                    yield {"id": str(event.seq), "data": event.model_dump_json()}
            finally:
                session.bus.unsubscribe(queue)
                session.desktop.subscribers_changed()  # an op waiting on a vanished island ends now

        return EventSourceResponse(stream(), ping=PING_S)

    @app.get("/settings/voice")
    async def get_voice_settings():
        profile = await asyncio.to_thread(voice_profile.load)  # the file, then the environment on top
        return voice_settings.settings_from(profile, core.active_engine())

    @app.patch("/settings/voice")
    async def patch_voice_settings(request: Request):
        """Validate (422 invalid_setting), refuse env-pinned fields (409 set_by_environment), save the
        file, apply to the voice, and return what is now in effect. Nothing is saved when any check fails."""
        try:
            raw = await request.json()
        except ValueError:
            return _invalid_setting("body")
        try:
            patch = VoiceSettingsPatch.model_validate(raw)
        except ValidationError as exc:
            return _invalid_setting(voice_settings.invalid_field(exc))
        pinned = voice_settings.pinned_in_patch(patch, os.environ)
        if pinned is not None:
            return JSONResponse({"error": "set_by_environment", "field": pinned}, status_code=409)

        path = voice_profile.default_path()
        before = core.voice_state()
        stored = await asyncio.to_thread(voice_profile.load, path, {})  # the file alone: no env values written back
        voice_settings.apply_patch(patch, stored)
        await asyncio.to_thread(voice_profile.save, stored, path)
        effective = await asyncio.to_thread(voice_profile.load, path)
        core.apply_voice_profile(effective)
        after = core.voice_state()
        if after != before:  # the engine may change without the ready state changing, so the watcher would miss it
            await core.broadcast_voice_status(after[0], after[1])
        return voice_settings.settings_from(effective, core.active_engine())

    @app.get("/voices")
    async def get_voices():
        return await voice_settings.list_voices()

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

    @app.post("/desktop/{op_id}/result", status_code=204)
    async def desktop_result(op_id: str, body: DesktopResult):
        session = core.find_desktop_op(op_id)
        if not session.desktop.resolve(op_id, body):
            raise HTTPException(404, {"error": "desktop_op_not_found"})
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
                if getattr(text, "dropped", None) == "silent":  # a held key with no sound; never a tap
                    await session.no_speech()

            asyncio.create_task(finish())  # the transcript arrives as events
        else:
            raise HTTPException(422, {"error": "bad_action"})
        return Response(status_code=204)

    @app.post("/sessions/{sid}/diagnostics", status_code=204)
    async def diagnostics(sid: str, body: ClientDiagnostic):
        """A problem only the client can see (e.g. its global shortcut couldn't be registered),
        spoken by the core like any other error. Fixed sentences per code; once per code and chord
        per session (the client announces a repeat of the same one itself)."""
        session = core.session(sid)
        key = (body.code, body.shortcut)  # a different failing chord is news; the same one isn't
        if key not in session.reported:
            session.reported.add(key)
            message, recovery = diagnostic_text(body)
            await session.error(body.code, message, recovery)
        return Response(status_code=204)

    @app.post("/sessions/{sid}/ready", status_code=204)
    async def ready(sid: str, body: ClientReady):
        """The client finished setting up: say so once per session, naming the push-to-talk chord
        it registered, so the first thing a blind user hears is how to start. Fixed text, like the
        diagnostics; normal priority, so it never talks over an error."""
        session = core.session(sid)
        session.desktop_ready = body.desktop
        if not session.greeted:
            session.greeted = True
            await session.say(None, ready_text(body.shortcut))
        return Response(status_code=204)

    @app.post("/speech", status_code=204)
    async def speech(body: SpeechBody):
        if body.action == "stop":
            await core.voice.stop_speech()
        elif body.action == "repeat":
            await core.voice.repeat_last()
        elif body.action == "sample":
            # the voice picker's Test voice: a fixed line, normal priority; a voice with no announcer stays silent
            announcer = getattr(core.voice, "announcer", None)
            if announcer is not None:
                last = announcer.last  # the announcer records every normal line as "last"; "repeat" must keep the real one
                await core.voice.announce(SAMPLE_TEXT, "normal")
                announcer.last = last
        else:
            raise HTTPException(422, {"error": "bad_action"})
        return Response(status_code=204)

    return app


def _invalid_setting(field: str) -> JSONResponse:
    return JSONResponse({"error": "invalid_setting", "field": field}, status_code=422)


async def _probe_python(runner) -> str | None:
    """None if the runner found a Python, else the spoken NoInterpreter message. Off the loop:
    `uv python find` and friends are subprocesses."""
    try:
        await asyncio.to_thread(lambda: runner.python)
    except NoInterpreter as exc:
        return str(exc)
    except Exception:  # an odd failure here must not block sessions; a run will report it
        log.exception("interpreter pre-check failed")
    return None


def diagnostic_text(d: ClientDiagnostic) -> tuple[str, str]:
    if d.code == "shortcut_registration_failed":
        chord = d.shortcut.replace("+", " ") if d.shortcut else "for push-to-talk"
        return (
            f"The push-to-talk shortcut {chord} couldn't be registered. Another app may be using it.",
            "Use the Start listening button in Artemis, or choose a different shortcut.",
        )
    raise AssertionError(d.code)  # unreachable: the model only accepts known codes


def ready_text(shortcut: str | None) -> str:
    if shortcut:
        return f"Artemis is ready. Hold {shortcut.replace('+', ' ')} and speak, or type a request."
    return "Artemis is ready. Use the Start listening button and speak, or type a request."


def mic_message(exc: Exception) -> str:
    text = str(exc).strip()
    return text if text and len(text) < 160 else "The microphone didn't work."


