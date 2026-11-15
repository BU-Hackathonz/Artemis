"""Spoken output: NVDA's own voice when NVDA runs, Windows SAPI otherwise, never both.

The router checks NVDA on every call, so starting NVDA after the core still stops SAPI
from talking over it (architecture.md risk 3). NVDA detection looks for NVDA's window and does
not depend on the controller DLL: if NVDA runs but the DLL is missing, Artemis stays silent and
reports voice_ready=false so the client's live region speaks through NVDA instead.

Setup (fetches the x64 DLL and the Whisper model): python -m artemis.voice.setup
"""

from __future__ import annotations

import asyncio
import ctypes
import inspect
import logging
import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

from artemis.contracts.events import Priority
from artemis.policy.redact import redact

if TYPE_CHECKING:  # profile.py imports this module, so the runtime import would be circular
    from artemis.voice.profile import VoiceProfile

log = logging.getLogger(__name__)

# The DLL ships in NVDA's controllerClient zip; it is not committed. Only these two paths are tried.
NVDA_DLL = Path(os.environ.get("ARTEMIS_NVDA_DLL") or Path(__file__).parent / "bin" / "nvdaControllerClient.dll")


# SAPI rates run -10..10 (0 = normal). Past about 6 words blur together; below -3 drags.
RATE_MIN, RATE_MAX = -3, 6


def clamp_rate(rate: int) -> int:
    return max(RATE_MIN, min(RATE_MAX, int(rate)))


def _env_rate() -> int | None:
    """ARTEMIS_SAPI_RATE, clamped. None when unset or invalid: then the user's own Windows rate stays."""
    value = os.environ.get("ARTEMIS_SAPI_RATE")
    if value is None or not value.strip():
        return None
    try:
        return clamp_rate(int(value))
    except ValueError:
        log.warning("ARTEMIS_SAPI_RATE=%r is not a number; keeping the Windows speech rate", value)
        return None


def nvda_running() -> bool:
    """True while NVDA runs. NVDA keeps a hidden window of class wxWindowClassNR titled "NVDA"."""
    if sys.platform != "win32":
        return False
    find = ctypes.windll.user32.FindWindowW
    find.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    return bool(find("wxWindowClassNR", "NVDA"))


class RateStatus(str, Enum):
    """What a rate change did. The core says the matching line; the voice never speaks it."""

    FASTER = "faster"
    SLOWER = "slower"
    AT_MAX = "at_max"  # already at our fastest: nothing changed
    AT_MIN = "at_min"  # already at our slowest: nothing changed
    NVDA = "nvda"  # NVDA's own speech settings are the user's: never touched
    NO_OUTPUT = "no_output"  # no voice output at all, so there is no rate to change
    UNCHANGED = "none"  # a zero step


class Speaker(Protocol):
    name: str

    def available(self) -> bool: ...
    def speak(self, text: str, interrupt: bool) -> None: ...
    def cancel(self) -> None: ...
    def busy(self) -> bool: ...


def speech_seconds(text: str, words_per_second: float) -> float:
    """Rough time to say `text`: its word count at the given pace."""
    return len(text.split()) / words_per_second


class NvdaSpeaker:
    """NVDA Controller Client: speaks in the user's own NVDA voice and rate, from any focused app."""

    name = "nvda"
    # The controller can't say whether NVDA is still talking, so busy() estimates it from word count.
    # 3.5 words a second is an assumption, not measured on NVDA. The error runs both ways: if the user's
    # NVDA is slower, a low line can still be spoken over our last line; if it is faster, a low line can
    # be dropped after NVDA has already gone quiet. Normal and high lines are never dropped by busy().
    WORDS_PER_SECOND = 3.5

    def __init__(self, dll_path: Path = NVDA_DLL, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._done_at = 0.0  # when the text we sent should have finished
        self._dll = None
        if sys.platform == "win32" and dll_path.is_file():
            try:
                dll = ctypes.WinDLL(str(dll_path))
                dll.nvdaController_speakText.argtypes = [ctypes.c_wchar_p]
                dll.nvdaController_brailleMessage.argtypes = [ctypes.c_wchar_p]
                self._dll = dll
            except (OSError, AttributeError) as e:  # e.g. the x86 DLL in 64-bit Python
                log.warning("NVDA controller DLL %s failed to load: %s", dll_path, e)
                self._dll = None

    def available(self) -> bool:
        # testIfRunning returns 0 when NVDA is running and reachable through the DLL.
        return self._dll is not None and self._dll.nvdaController_testIfRunning() == 0

    def speak(self, text: str, interrupt: bool) -> None:
        now = self._clock()
        if interrupt:
            self._dll.nvdaController_cancelSpeech()
            self._done_at = now
        self._dll.nvdaController_speakText(text)
        self._dll.nvdaController_brailleMessage(text)
        # Without interrupt NVDA queues the text after whatever it is still saying.
        self._done_at = max(self._done_at, now) + speech_seconds(text, self.WORDS_PER_SECOND)

    def cancel(self) -> None:
        self._dll.nvdaController_cancelSpeech()
        self._done_at = 0.0

    def braille(self, text: str) -> None:
        """Send text to the braille display only; NVDA does not speak it."""
        self._dll.nvdaController_brailleMessage(text)

    def busy(self) -> bool:
        # An estimate of our own text only: NVDA reading the user's code is invisible to the controller.
        return self._clock() < self._done_at


class SapiSpeaker:
    """Windows SAPI 5 through COM. Asynchronous, so the event loop never blocks on speech.

    COM is initialized on the constructing thread: build the Announcer and call announce() on the
    event-loop thread only, never through asyncio.to_thread.
    """

    name = "sapi"
    _ASYNC, _PURGE = 1, 2  # SVSFlagsAsync, SVSFPurgeBeforeSpeak
    _SPEAKING = 2  # SRSEIsSpeaking

    def __init__(self, voice_name: str | None = None, rate: int | None = None) -> None:
        self._voice = None
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            self._voice = win32com.client.Dispatch("SAPI.SpVoice")
        except Exception:  # pywin32 missing or COM unavailable: report unavailable, don't crash the core
            self._voice = None
            return
        # Both optional. Without them the user's own Windows voice and rate stay as they are.
        self.choose_voice(voice_name if voice_name is not None else os.environ.get("ARTEMIS_SAPI_VOICE"))
        rate = rate if rate is not None else _env_rate()
        if rate is not None:
            try:
                self.set_rate(rate)
            except Exception as e:  # a COM hiccup here must never stop the core from starting
                log.warning("Could not set the SAPI rate: %s", type(e).__name__)

    def available(self) -> bool:
        return self._voice is not None

    def choose_voice(self, name: str | None) -> bool:
        """Use the first installed voice whose description contains `name`; keep the default if none does."""
        if not name:
            return False
        try:
            for token in self._voice.GetVoices():
                if name.lower() in token.GetDescription().lower():
                    self._voice.Voice = token
                    return True
        except Exception:
            pass
        log.warning("SAPI voice %r is not installed; keeping the Windows default voice", name)
        return False

    @property
    def rate(self) -> int:
        return int(self._voice.Rate)

    def set_rate(self, rate: int) -> int:
        """Set SAPI's rate, clamped to RATE_MIN..RATE_MAX so speech never becomes unintelligible."""
        self._voice.Rate = clamp_rate(rate)
        return self.rate

    def speak(self, text: str, interrupt: bool) -> None:
        self._voice.Speak(text, self._ASYNC | (self._PURGE if interrupt else 0))

    def cancel(self) -> None:
        self._voice.Speak("", self._ASYNC | self._PURGE)

    def busy(self) -> bool:
        return self._voice.Status.RunningState == self._SPEAKING


# ----- ElevenLabs (opt-in: ARTEMIS_VOICE_ENGINE=elevenlabs; used only when NVDA is not running) -----

ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
ELEVEN_PCM_RATE = 22_050  # output_format=pcm_22050: 16-bit mono, playable as each chunk arrives
SPEED_MIN, SPEED_MAX, SPEED_STEP = 0.7, 1.2, 0.1
FAILURES_BEFORE_SAPI = 3
FALLBACK_NOTICE = "My natural voice isn't reachable, so I'm using the Windows voice."
# Short fixed lines worth keeping as audio so they play at once. Only these exact strings are cached:
# never transcripts, code, file contents or anything the agent wrote.
CACHED_LINES = frozenset({
    "Artemis is ready. Hold Control Shift Space and speak, or type a request.",
    "Artemis is ready. Use the Start listening button and speak, or type a request.",
    "Canceled.",
    "Canceled. Nothing was written.",
    "Speaking faster.",
    "Speaking slower.",
    "That's the fastest I go.",
    "That's the slowest I go.",
    "Still working.",
})
PLACEHOLDER_VOICE = "<PASTE_VOICE_ID>"  # what the setup docs show; treated as unset
DEFAULT_VOICE_ID = "fPIfC3elMLbN9tNwMXkw"  # Artemis's voice, chosen by Avery; ARTEMIS_ELEVEN_VOICE_ID overrides it


def _env_float(name: str, default: float, low: float | None = None, high: float | None = None) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        log.warning("%s=%r is not a number; using %s", name, raw, default)
        return default
    if value != value:  # nan
        return default
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def clamp_speed(speed: float) -> float:
    return round(max(SPEED_MIN, min(SPEED_MAX, float(speed))), 2)


def _open_stream(request: urllib.request.Request, timeout_s: float):
    return urllib.request.urlopen(request, timeout=timeout_s)  # noqa: S310 (fixed https URL)


def _output_stream():
    import sounddevice as sd

    return sd.RawOutputStream(samplerate=ELEVEN_PCM_RATE, channels=1, dtype="int16")


def _chain(first: bytes, rest):
    yield first
    yield from rest


ENGINES = ("elevenlabs", "nvda", "sapi")


def voice_engine() -> str:
    """ARTEMIS_VOICE_ENGINE picks Artemis's own voice: elevenlabs (the default), nvda or sapi."""
    raw = os.environ.get("ARTEMIS_VOICE_ENGINE", "").strip().lower()
    if raw and raw not in ENGINES:
        log.warning("ARTEMIS_VOICE_ENGINE=%r is not one of %s; using elevenlabs", raw, ", ".join(ENGINES))
    return raw if raw in ENGINES else "elevenlabs"


class ElevenLabsSpeaker:
    """ElevenLabs streaming text-to-speech, played as the first audio chunk arrives.

    The network and the audio device run on one worker thread, so the event loop never waits on them.
    A line that can't start within the timeout (no key, a failed request, a slow first chunk) is spoken
    by the fallback speaker instead (NVDA when it runs, else SAPI). After FAILURES_BEFORE_SAPI failures
    in a row this speaker reports unavailable for the rest of the session, so the router falls back
    too. When NVDA runs, every line also goes to the braille display through `braille`. The API key is
    only ever sent in the request header: it is never logged, spoken or put in an event.
    """

    name = "elevenlabs"

    def __init__(self, api_key: str | None = None, voice_id: str | None = None, model: str | None = None,
                 speed: float | None = None, stability: float | None = None, timeout_s: float | None = None,
                 fallback: Speaker | None = None, opener=_open_stream, output=_output_stream,
                 on_give_up: Callable[[str], None] | None = None) -> None:
        self._key = api_key if api_key is not None else os.environ.get("ELEVENLABS_API_KEY", "").strip()
        env_voice = os.environ.get("ARTEMIS_ELEVEN_VOICE_ID", "").strip()
        self.voice_id = voice_id or (env_voice if env_voice and env_voice != PLACEHOLDER_VOICE else DEFAULT_VOICE_ID)
        self.model = model or os.environ.get("ARTEMIS_ELEVEN_MODEL", "").strip() or "eleven_flash_v2_5"
        self.speed = clamp_speed(speed if speed is not None else _env_float("ARTEMIS_ELEVEN_SPEED", 1.0))
        self.stability = stability if stability is not None else _env_float("ARTEMIS_ELEVEN_STABILITY", 0.5, 0.0, 1.0)
        self.timeout_s = timeout_s if timeout_s is not None else _env_float("ARTEMIS_ELEVEN_TIMEOUT_S", 1.5, 0.2, 10.0)
        self.fallback = fallback
        self.on_give_up = on_give_up  # the core can set this to say FALLBACK_NOTICE through the bus
        self.braille: Callable[[str], None] | None = None  # set by the Announcer: NVDA's braille display
        self._opener, self._output = opener, output
        self._queue: queue.Queue = queue.Queue()
        self._gen = 0  # bumped by cancel (and so by interrupt): work holding an older number stops
        self._lock = threading.Lock()
        self._playing = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._stream = None  # the open output stream, so cancel() can abort it from the loop thread
        self._response = None  # the open HTTP response, so cancel() can abandon the download
        self._failures = 0
        self.gave_up = False
        self._cache: dict[tuple, bytes] = {}
        self.last_first_audio_s: float | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker: threading.Thread | None = None
        if not self._key:
            log.warning("ElevenLabs voice: ELEVENLABS_API_KEY is not set; using NVDA or the Windows voice")
        elif self.voice_id == PLACEHOLDER_VOICE:
            log.warning("ElevenLabs voice: ARTEMIS_ELEVEN_VOICE_ID is not set; using NVDA or the Windows voice")

    # Speaker protocol

    def available(self) -> bool:
        return bool(self._key) and self.voice_id != PLACEHOLDER_VOICE and not self.gave_up

    def speak(self, text: str, interrupt: bool) -> None:
        try:
            self._loop = asyncio.get_running_loop()  # the SAPI fallback must run on the loop's thread (COM)
        except RuntimeError:
            self._loop = None
        if interrupt:
            self.cancel()
        if self.braille is not None:
            try:
                self.braille(text)  # the same line on the braille display, as NVDA would send it
            except Exception:
                pass
        with self._lock:
            gen = self._gen
            self._idle.clear()
            self._queue.put((gen, text))
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, name="artemis-elevenlabs", daemon=True)
                self._worker.start()

    def cancel(self) -> None:
        with self._lock:
            self._gen += 1
            stream, response = self._stream, self._response
            try:
                while True:
                    self._queue.get_nowait()
            except queue.Empty:
                pass
        for thing, how in ((stream, "abort"), (response, "close")):
            if thing is not None:
                try:
                    getattr(thing, how)()  # stop the sound now, and abandon the download
                except Exception:
                    pass
        if self.fallback is not None:
            try:
                self.fallback.cancel()  # a line that fell back stops too (push-to-talk)
            except Exception:
                pass

    def busy(self) -> bool:
        return self._playing.is_set() or not self._queue.empty()

    # Speed: "faster" and "slower"

    def step_speed(self, delta: int) -> RateStatus:
        if not delta:
            return RateStatus.UNCHANGED
        after = clamp_speed(self.speed + (SPEED_STEP if delta > 0 else -SPEED_STEP))
        if after == self.speed:
            return RateStatus.AT_MAX if delta > 0 else RateStatus.AT_MIN
        self.speed = after
        return RateStatus.FASTER if delta > 0 else RateStatus.SLOWER

    # Worker thread

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """For tests and measurements: wait until everything queued was played or dropped."""
        return self._idle.wait(timeout)

    def _current(self, gen: int) -> bool:
        with self._lock:
            return gen == self._gen

    def _run(self) -> None:
        while True:
            try:
                gen, text = self._queue.get(timeout=0.5)
            except queue.Empty:
                with self._lock:
                    if self._queue.empty():
                        self._idle.set()
                        self._worker = None
                        return
                continue
            if self._current(gen):
                self._say(gen, text)
            with self._lock:
                if self._queue.empty():
                    self._idle.set()

    def _say(self, gen: int, text: str) -> None:
        key = (self.voice_id, self.model, self.speed, text)
        cached = self._cache.get(key)
        if cached is not None:
            self.last_first_audio_s = 0.0
            self._play(iter([cached]), gen)
            return
        started = time.monotonic()
        try:
            chunks = self._fetch(text)
            first = next(chunks)
        except StopIteration:
            self._failed(text, gen, "the stream returned no audio")
            return
        except Exception as e:
            self._failed(text, gen, self._reason(e))
            return
        self.last_first_audio_s = time.monotonic() - started
        self._failures = 0
        played = self._play(_chain(first, chunks), gen)
        if played is not None and text in CACHED_LINES:
            self._cache[key] = played

    def _fetch(self, text: str):
        # The only place text leaves the computer: redact here, so answers, "Heard:" transcripts and
        # error lines that never passed the gateway are scrubbed too. The fallback speakers are local.
        body = json.dumps({
            "text": redact(text),
            "model_id": self.model,
            "voice_settings": {"stability": self.stability, "similarity_boost": 0.75,
                               "use_speaker_boost": True, "speed": self.speed},
        }).encode("utf-8")
        url = ELEVEN_URL.format(voice_id=urllib.parse.quote(self.voice_id, safe="")) + f"?output_format=pcm_{ELEVEN_PCM_RATE}"
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={"xi-api-key": self._key, "Content-Type": "application/json", "Accept": "audio/pcm"},
        )
        response = self._opener(request, self.timeout_s)
        with self._lock:
            self._response = response
        try:
            while True:
                chunk = response.read(4096)
                if not chunk:
                    return
                yield chunk
        finally:
            with self._lock:
                self._response = None
            try:
                response.close()
            except Exception:
                pass

    def _play(self, chunks, gen: int) -> bytes | None:
        """Play PCM chunks as they come. Returns the whole audio if it played to the end, else None."""
        stream = self._output()
        with self._lock:
            if gen != self._gen:
                return None
            self._stream = stream
        kept = bytearray()
        self._playing.set()
        try:
            stream.start()
            for chunk in chunks:
                if not self._current(gen):
                    return None
                stream.write(chunk)
                kept += chunk
            stream.stop()  # drains the buffer, so busy() stays true until the last word is heard
            return bytes(kept) if self._current(gen) else None
        except Exception as e:
            if self._current(gen):
                log.warning("ElevenLabs playback stopped: %s", type(e).__name__)
            return None
        finally:
            self._playing.clear()
            with self._lock:
                self._stream = None
            try:
                stream.close()
            except Exception:
                pass

    @staticmethod
    def _reason(e: Exception) -> str:
        """A short reason for the log. Never the request, its headers or body: the key lives there."""
        if isinstance(e, urllib.error.HTTPError):
            return f"HTTP {e.code}"
        if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
            return "no audio within the timeout"
        return type(e).__name__

    def _failed(self, text: str, gen: int, reason: str) -> None:
        if not self._current(gen):
            return  # canceled meanwhile: say nothing
        self._failures += 1
        log.warning("ElevenLabs failed (%s); this line uses the Windows voice", reason)
        if self._failures >= FAILURES_BEFORE_SAPI and not self.gave_up:
            self.gave_up = True
            log.warning("ElevenLabs failed %d times in a row; using the Windows voice for this session", self._failures)
            if self.on_give_up is not None:
                self._on_loop(self.on_give_up, FALLBACK_NOTICE)
            else:
                log.warning("Not spoken yet (the core doesn't wire it): %s", FALLBACK_NOTICE)
        if self.fallback is not None and self.fallback.available():
            self._on_loop(self.fallback.speak, text, False)

    def _on_loop(self, fn, *args) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(fn, *args)
        else:
            fn(*args)


class NullSpeaker:
    """No voice output. The core reports voice_ready=false so the client's live region speaks instead."""

    name = "none"

    def available(self) -> bool:
        return True

    def speak(self, text: str, interrupt: bool) -> None:
        print(f"[announce] {text}", file=sys.stderr)

    def cancel(self) -> None:
        pass

    def busy(self) -> bool:
        return False


class _Fallback:
    """Where a line goes when ElevenLabs can't say it: NVDA when it runs (never a second voice over it),
    else SAPI. cancel() stops only the speaker this fallback last used, so NVDA's own reading of the
    screen is never cut off by Artemis."""

    name = "fallback"

    def __init__(self, announcer: "Announcer") -> None:
        self._a, self._last = announcer, None

    def _target(self) -> Speaker | None:
        a = self._a
        if a.is_nvda_running():
            return a.nvda if a.nvda.available() else None
        return a.sapi if a.sapi.available() else None

    def available(self) -> bool:
        return self._target() is not None

    def speak(self, text: str, interrupt: bool) -> None:
        target = self._target()
        if target is not None:
            self._last = target
            target.speak(text, interrupt)

    def cancel(self) -> None:
        if self._last is not None:
            self._last.cancel()
            self._last = None

    def busy(self) -> bool:
        return self._last is not None and self._last.busy()


class Announcer:
    """announce() for the core. Artemis's voice is ARTEMIS_VOICE_ENGINE: elevenlabs (the default) speaks
    even while NVDA runs, and NVDA keeps the screen and the braille display; with no key, or with nvda
    or sapi chosen, it is NVDA when it runs, else SAPI, else silence. Never two voices for one line."""

    def __init__(self, nvda: Speaker | None = None, sapi: Speaker | None = None, is_nvda_running: Callable[[], bool] = nvda_running,
                 eleven: Speaker | None = None) -> None:
        self.nvda = nvda or NvdaSpeaker()
        self.sapi = sapi or SapiSpeaker()
        self.is_nvda_running = is_nvda_running
        self.engine = voice_engine()
        if eleven is None and self.engine == "elevenlabs":
            eleven = ElevenLabsSpeaker(fallback=_Fallback(self))
        if eleven is not None and hasattr(eleven, "braille"):
            eleven.braille = self._braille
        self.eleven = eleven
        self.last: str | None = None
        self._warned = False
        self.working_cue_earcon = False  # set by apply(); listen.py still reads ARTEMIS_WORKING_CUE (wired with the settings route)

    def _braille(self, text: str) -> None:
        if self.is_nvda_running() and self.nvda.available() and hasattr(self.nvda, "braille"):
            self.nvda.braille(text)

    def apply(self, profile: "VoiceProfile") -> None:
        """Switch Artemis's voice from the next line on. A line already playing is not cancelled.

        Runs on the event-loop thread and does no network I/O: the ElevenLabs worker thread does the
        requests. NVDA's voice and rate are never touched here.
        """
        self.engine = profile.engine
        self.working_cue_earcon = profile.working_cue
        if self.engine == "elevenlabs" and self.eleven is None:
            eleven = ElevenLabsSpeaker(fallback=_Fallback(self))
            if hasattr(eleven, "braille"):
                eleven.braille = self._braille
            self.eleven = eleven
        if self.eleven is not None:
            if profile.eleven_voice_id is not None:
                self.eleven.voice_id = profile.eleven_voice_id
            self.eleven.speed = clamp_speed(profile.eleven_speed)  # the cache key holds voice and speed: no flush
        if self.sapi.available():
            if profile.sapi_voice is not None:
                try:
                    self.sapi.choose_voice(profile.sapi_voice)
                except Exception as e:  # a COM hiccup must not stop the voice change
                    log.warning("Could not choose the SAPI voice: %s", type(e).__name__)
            if profile.sapi_rate is not None:
                try:
                    self.sapi.set_rate(profile.sapi_rate)
                except Exception as e:
                    log.warning("Could not set the SAPI rate: %s", type(e).__name__)

    def active_engine(self) -> str:
        """The name of the speaker that would speak the next line: elevenlabs, nvda, sapi or none."""
        return self.current().name

    def current(self) -> Speaker:
        if self.engine == "elevenlabs" and self.eleven is not None and self.eleven.available():
            return self.eleven  # the user's chosen voice, with or without NVDA
        if self.is_nvda_running():
            if self.nvda.available():
                return self.nvda
            if not self._warned:  # never let SAPI talk over a running NVDA
                log.warning("NVDA is running but its controller DLL is unavailable; staying silent. Run: python -m artemis.voice.setup")
                self._warned = True
            return NullSpeaker()
        return self.sapi if self.sapi.available() else NullSpeaker()

    def status(self) -> tuple[bool, str | None]:
        """(voice_ready, screen_reader) as contracts.md voice.status and /health report them.

        screen_reader is "nvda" only when Artemis speaks through NVDA: with ElevenLabs speaking it is
        None, so "faster" reaches ElevenLabs instead of the NVDA rate line.
        """
        name = self.current().name
        return name != "none", "nvda" if name == "nvda" else None

    @property
    def voice_ready(self) -> bool:
        return self.status()[0]

    @property
    def screen_reader(self) -> str | None:
        return self.status()[1]

    async def watch(self, callback: Callable[[bool, str | None], object], interval_s: float = 2.0) -> None:
        """Call callback(voice_ready, screen_reader) now and on every change, e.g. NVDA started while idle.

        Run as asyncio.create_task(announcer.watch(cb)); cancel the task to stop. callback may be async.
        """
        last = None
        while True:
            now = self.status()
            if now != last:
                last = now
                try:
                    result = callback(*now)
                    if inspect.isawaitable(result):
                        await result
                except Exception:  # one failed emit must not end status updates for the session
                    log.exception("voice status callback failed")
            await asyncio.sleep(interval_s)

    async def announce(self, text: str, priority: Priority) -> None:
        text = text.strip()
        if not text:
            return
        speaker = self.current()
        if priority == "low" and speaker.busy():
            return  # contracts.md: low may be dropped while speech is busy
        if priority != "low":
            self.last = text
        speaker.speak(text, interrupt=priority == "high")

    async def stop_speech(self) -> None:
        self.current().cancel()

    async def repeat_last(self) -> None:
        if self.last:
            self.current().speak(self.last, interrupt=True)

    async def set_rate(self, delta: int) -> RateStatus:
        """'Slower' / 'faster' from the conversation controller. Speaks nothing: the caller says the outcome.

        Only SAPI's rate is ours to change. Under NVDA the voice and rate are the user's own settings,
        so they are never touched.
        """
        speaker = self.current()
        if speaker.name == "nvda":
            return RateStatus.NVDA
        if hasattr(speaker, "step_speed"):  # ElevenLabs: speed 0.7..1.2 in steps of 0.1, for this session
            return speaker.step_speed(delta)
        if not hasattr(speaker, "set_rate"):
            return RateStatus.NO_OUTPUT  # no voice output at all; there is nothing to make faster or slower
        if not delta:
            return RateStatus.UNCHANGED
        step = 1 if delta > 0 else -1
        before = speaker.rate
        # The user's own Windows rate can sit outside our range (we keep it unless asked). Stepping
        # from there must never go the wrong way: "faster" from 8 would clamp down to 6.
        at_limit = before >= RATE_MAX if step > 0 else before <= RATE_MIN
        after = before if at_limit else speaker.set_rate(before + step)
        if after == before:
            return RateStatus.AT_MAX if delta > 0 else RateStatus.AT_MIN
        return RateStatus.FASTER if delta > 0 else RateStatus.SLOWER
