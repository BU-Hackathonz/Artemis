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
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
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
PERMANENT_FAILURES = {"HTTP 401", "HTTP 402", "HTTP 403"}  # retrying can't help: give up at once
STREAM_TIMEOUT_S = 10.0  # one read after the first audio: a slow network stalls, it doesn't cut the line
FALLBACK_WAIT_S = 30.0  # the longest a queued line waits for the local voice to finish a fallen-back line
def _local_voice_name() -> str:
    return "NVDA or the Windows voice" if sys.platform == "win32" else "the offline voice"


def fallback_notice() -> str:
    """The give-up line. On Linux it never names Windows, SAPI or NVDA."""
    local = "the Windows voice" if sys.platform == "win32" else "the offline voice"
    return f"My natural voice isn't reachable, so I'm using {local}."


FALLBACK_NOTICE = fallback_notice()
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
DEFAULT_VOICE_ID = "SAxJUlDKRc79XAyeWyMu"  # Morgan (Deep, Powerful, and Confident), chosen by Yaovi; ARTEMIS_ELEVEN_VOICE_ID overrides it


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


class _Line:
    """One queued ElevenLabs line and its download (started early when it is next in line)."""

    def __init__(self, gen: int, text: str, priority: str) -> None:
        self.gen, self.text, self.priority = gen, text, priority
        self.chunks: queue.Queue = queue.Queue()  # audio chunks, then None at the end, or the error
        self.started = False
        self.abandoned = threading.Event()
        self.response = None

    def abandon(self) -> None:
        """Stop the download and never play this line: canceled, or stale."""
        self.abandoned.set()
        self.close()

    def close(self) -> None:
        response, self.response = self.response, None
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


class ElevenLabsSpeaker:
    """ElevenLabs streaming text-to-speech, played as the first audio chunk arrives.

    The audio device runs on one worker thread and each download on its own, so the event loop never
    waits on them. Lines play in order: only an interrupt cuts the one playing, a newer line drops a
    progress line not heard yet, and the next queued line downloads while this one plays. A line that
    can't start within the timeout (no key, a failed request, a slow first chunk) is spoken by the
    fallback speaker instead (NVDA when it runs, else SAPI, else speech-dispatcher), and ElevenLabs
    waits for it to finish before its next line. Every fallback is logged with its reason. After FAILURES_BEFORE_SAPI failures
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
        self._pending: deque[_Line] = deque()  # lines waiting their turn, oldest first
        self._now: _Line | None = None  # the line the worker is fetching or playing
        self._gen = 0  # bumped by cancel (and so by interrupt): work holding an older number stops
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._playing = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._stream = None  # the open output stream, so cancel() can abort it from the loop thread
        self._failures = 0
        self._handed: threading.Event | None = None  # set when a line went to the local voice
        self.gave_up = False
        self._cache: dict[tuple, bytes] = {}
        self.last_first_audio_s: float | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker: threading.Thread | None = None
        if not self._key:
            log.warning("ElevenLabs voice: ELEVENLABS_API_KEY is not set; using %s", _local_voice_name())
        elif self.voice_id == PLACEHOLDER_VOICE:
            log.warning("ElevenLabs voice: ARTEMIS_ELEVEN_VOICE_ID is not set; using %s", _local_voice_name())

    # Speaker protocol

    def available(self) -> bool:
        return bool(self._key) and self.voice_id != PLACEHOLDER_VOICE and not self.gave_up

    def speak(self, text: str, interrupt: bool, priority: Priority | None = None) -> None:
        """Queue a line. Only an interrupt (a high line) cuts the line playing; a normal line waits its
        turn. Any new line drops a low progress line that is not audible yet: it is stale."""
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
            stale = [line for line in self._pending if line.priority == "low"]
            for old in stale:
                self._pending.remove(old)
            if self._now is not None and self._now.priority == "low" and not self._playing.is_set():
                stale.append(self._now)
            for old in stale:
                old.abandoned.set()  # the worker skips it; its download is closed below, off the lock
            line = _Line(self._gen, text, priority or ("high" if interrupt else "normal"))
            self._idle.clear()
            self._pending.append(line)
            self._wake.notify()
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, name="artemis-elevenlabs", daemon=True)
                self._worker.start()
        for old in stale:
            old.close()

    def cancel(self) -> None:
        with self._lock:
            self._gen += 1
            stream = self._stream
            dropped = list(self._pending) + ([self._now] if self._now is not None else [])
            self._pending.clear()
        for line in dropped:
            line.abandon()  # abandon the downloads, the prefetched one too
        if stream is not None:
            try:
                stream.abort()  # stop the sound now
            except Exception:
                pass
        if self.fallback is not None:
            try:
                self.fallback.cancel()  # a line that fell back stops too (push-to-talk)
            except Exception:
                pass

    def busy(self) -> bool:
        with self._lock:
            return self._playing.is_set() or self._now is not None or bool(self._pending)

    def speaking(self) -> bool:
        """Audio is playing now (a queued or downloading line is not speech yet)."""
        return self._playing.is_set()

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
            with self._lock:
                if not self._pending:
                    self._wake.wait(0.5)
                if not self._pending:
                    self._now = None
                    self._idle.set()
                    self._worker = None
                    return
                line = self._now = self._pending.popleft()
            if line.gen == self._gen_now() and not line.abandoned.is_set():
                self._say(line)
            with self._lock:
                self._now = None
                if not self._pending:
                    self._idle.set()

    def _gen_now(self) -> int:
        with self._lock:
            return self._gen

    def _cache_key(self, text: str) -> tuple:
        return (self.voice_id, self.model, self.speed, text)

    def _prefetch_next(self) -> None:
        """Start downloading the next queued line while this one plays, so it starts with no gap."""
        with self._lock:
            line = self._pending[0] if self._pending else None
        if line is not None and self._cache_key(line.text) not in self._cache:
            self._start(line)

    def _start(self, line: _Line) -> None:
        with self._lock:
            if line.started:
                return
            line.started = True
        threading.Thread(target=self._download, args=(line,), name="artemis-elevenlabs-fetch", daemon=True).start()

    def _download(self, line: _Line) -> None:
        """Fetch thread: put each audio chunk on the line's queue, then None at the end, or the error."""
        try:
            for chunk in self._fetch(line):
                if line.abandoned.is_set():
                    return
                line.chunks.put(chunk)
            line.chunks.put(None)
        except Exception as e:
            line.chunks.put(e)

    def _chunks(self, line: _Line, first: bytes):
        """The rest of the line's audio. A stall or error mid-line is logged with its reason."""
        yield first
        while True:
            try:
                item = line.chunks.get(timeout=STREAM_TIMEOUT_S)
            except queue.Empty:
                item = TimeoutError("timed out")
            if item is None:
                return
            if isinstance(item, Exception):
                if not line.abandoned.is_set():
                    log.warning("ElevenLabs stream stopped mid-line (%s); the rest of this line is lost",
                                self._reason(item))
                return
            yield item

    def _say(self, line: _Line) -> None:
        gen, text = line.gen, line.text
        key = self._cache_key(text)
        cached = self._cache.get(key)
        if cached is not None:
            self.last_first_audio_s = 0.0
            self._prefetch_next()
            self._hold_for_fallback(gen)
            self._play(iter([cached]), gen)
            return
        started = time.monotonic()
        self._start(line)  # already running when it was prefetched
        try:
            first = line.chunks.get(timeout=self.timeout_s)
        except queue.Empty:
            first = TimeoutError("timed out")
        if line.abandoned.is_set():
            return  # canceled, or a stale progress line: a newer line arrived before it was heard
        if first is None or isinstance(first, Exception):
            line.abandon()
            self._failed(line, "the stream returned no audio" if first is None else self._reason(first))
            return
        self.last_first_audio_s = time.monotonic() - started
        log.debug("ElevenLabs first audio in %.2f s", self.last_first_audio_s)
        self._failures = 0
        self._prefetch_next()
        self._hold_for_fallback(gen)
        played = self._play(self._chunks(line, first), gen)
        if played is not None and text in CACHED_LINES:
            self._cache[key] = played

    def _fetch(self, line: _Line):
        # The only place text leaves the computer: redact here, so answers, "Heard:" transcripts and
        # error lines that never passed the gateway are scrubbed too. The fallback speakers are local.
        body = json.dumps({
            "text": redact(line.text),
            "model_id": self.model,
            "voice_settings": {"stability": self.stability, "similarity_boost": 0.75,
                               "use_speaker_boost": True, "speed": self.speed},
        }).encode("utf-8")
        url = ELEVEN_URL.format(voice_id=urllib.parse.quote(self.voice_id, safe="")) + f"?output_format=pcm_{ELEVEN_PCM_RATE}"
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={"xi-api-key": self._key, "Content-Type": "application/json", "Accept": "audio/pcm"},
        )
        # The socket timeout covers each read; the first-audio deadline is timeout_s, kept by _say.
        response = self._opener(request, max(self.timeout_s, STREAM_TIMEOUT_S))
        line.response = response
        if line.abandoned.is_set():
            line.close()  # canceled while connecting
            return
        try:
            while True:
                chunk = response.read(4096)
                if not chunk:
                    return
                yield chunk
        finally:
            line.close()

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

    def _failed(self, line: _Line, reason: str) -> None:
        text, gen = line.text, line.gen
        if not self._current(gen):
            return  # canceled meanwhile: say nothing
        self._failures += 1
        if reason in PERMANENT_FAILURES:
            # A bad key, a plan without API access to this voice (402, e.g. a free key with a library voice),
            # or forbidden: no later line can succeed, so give up now instead of trying every line.
            self._failures = FAILURES_BEFORE_SAPI
        if self._failures >= FAILURES_BEFORE_SAPI and not self.gave_up:
            self.gave_up = True
            log.warning("ElevenLabs failed %d times in a row; using the local voice for this session", self._failures)
            notice = fallback_notice()
            if self.on_give_up is not None:
                self._on_loop(self.on_give_up, notice)
            else:
                log.warning("Not spoken yet (the core doesn't wire it): %s", notice)
        if self.fallback is None or not self.fallback.available():
            log.warning("ElevenLabs failed (%s) and no local voice is available; this line is not spoken", reason)
            return
        log.warning("ElevenLabs failed (%s); this line uses the local voice", reason)
        handed = threading.Event()

        def speak() -> None:
            try:
                self.fallback.speak(text, False)
            finally:
                handed.set()

        self._handed = handed
        self._on_loop(speak)

    def _hold_for_fallback(self, gen: int) -> None:
        """Before ElevenLabs plays again, let the local voice finish the line it was handed: two voices
        never overlap. Called once the next line's audio is ready, so the wait costs no download time."""
        handed, self._handed = self._handed, None
        if handed is None or self.fallback is None:
            return
        handed.wait(1.0)
        deadline = time.monotonic() + FALLBACK_WAIT_S
        while self._current(gen) and time.monotonic() < deadline:
            try:
                if not self.fallback.busy():
                    return
            except Exception:
                return
            time.sleep(0.05)

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


# ----- Linux: the offline voice (speech-dispatcher), and Orca detection -----


def orca_running(proc: Path = Path("/proc")) -> bool:
    """True while the Orca screen reader runs (Linux). Detection only: voice.status can't report it yet."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        for comm in proc.glob("[0-9]*/comm"):
            try:
                if comm.read_text(encoding="utf-8", errors="ignore").strip() == "orca":
                    return True
            except OSError:
                continue  # the process ended while we looked
    except OSError:
        pass
    return False


# Artemis's priorities as speech-dispatcher levels (approved by Avery). high speaks over Orca and Orca can't
# cut it; normal shares Orca's level, so each cuts the other; low never interrupts Orca and is dropped if
# anything else speaks.
SSIP_PRIORITY = {"high": "message", "normal": "text", "low": "notification"}


class SpeechDispatcherSpeaker:
    """Offline Linux voice: speech-dispatcher (ships with Ubuntu and Orca), spoken to directly over its
    SSIP socket with the standard library, no new dependency.

    Why not spd-say: its --cancel cancels every client's speech, Orca's included, and as a separate
    process it can land after the next line and wipe that out. On our own connection, CANCEL self stops
    only Artemis's messages, and commands are handled in order, so a cancel always lands before the
    line that follows it. busy() and speaking() come from speech-dispatcher's own BEGIN/END events.
    """

    name = "speechd"  # what Announcer.speaking_engine() reports (#141, the wake word)
    REPLY_TIMEOUT_S = 1.0
    RETRY_S = 5.0  # after a failed connect, don't try again on every announce

    def __init__(self, connect=None, clock: Callable[[], float] = time.monotonic) -> None:
        self._connect = connect or _speechd_connect
        self._clock = clock
        self._sock = None
        self._failed_at: float | None = None
        self._send_lock = threading.Lock()
        self._connect_lock = threading.Lock()  # one connection at a time, even if available() and speak() race
        self._replies: queue.Queue = queue.Queue()  # replaced with every connection (see _ensure)
        self._queued: set[str] = set()  # message ids speech-dispatcher still has for us
        self._playing = threading.Event()
        self._state = threading.Lock()
        self._level: str | None = None  # the SSIP priority last set on this connection

    # connection

    def _ensure(self):
        if self._sock is not None:
            return self._sock
        with self._connect_lock:
            if self._sock is not None:  # another thread connected while we waited
                return self._sock
            return self._open()

    def _open(self):
        if self._failed_at is not None and self._clock() - self._failed_at < self.RETRY_S:
            return None
        try:
            sock = self._connect()
            # Each connection gets its own reply queue: a reply that arrives after a command timed out
            # stays with the dead connection instead of answering the next connection's command.
            replies: queue.Queue = queue.Queue()
            self._replies = replies
            self._sock = sock
            threading.Thread(target=self._read, args=(sock, replies), name="artemis-speechd", daemon=True).start()
            self._command("SET self CLIENT_NAME user:artemis:voice")
            self._command("SET self NOTIFICATION all on")
            self._failed_at = None
            return sock
        except Exception as e:  # no speech-dispatcher: the router falls back to silence
            log.warning("speech-dispatcher is not reachable: %s", type(e).__name__)
            self._drop()
            self._failed_at = self._clock()
            return None

    def _drop(self) -> None:
        sock, self._sock = self._sock, None
        self._level = None  # a new connection starts at speech-dispatcher's default again
        with self._state:
            self._queued.clear()
        self._playing.clear()
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    def _send(self, *lines: str) -> None:
        self._sock.sendall("".join(line + "\r\n" for line in lines).encode("utf-8"))

    def _reply(self) -> list[str]:
        lines = self._replies.get(timeout=self.REPLY_TIMEOUT_S)
        if not lines[-1][:1] == "2":  # 2xx is success; 3xx/4xx/5xx are errors
            raise OSError(f"speech-dispatcher said {lines[-1][:3]}")
        return lines

    def _command(self, line: str) -> list[str]:
        with self._send_lock:
            self._send(line)
            return self._reply()

    def _read(self, sock, replies: queue.Queue) -> None:
        """Reader thread: split replies (to the waiting command) from events (7xx, which update state)."""
        buffer, block = b"", []
        try:
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                buffer += data
                while b"\r\n" in buffer:
                    raw, buffer = buffer.split(b"\r\n", 1)
                    line = raw.decode("utf-8", "replace")
                    block.append(line)
                    if len(line) >= 4 and line[3] == "-":
                        continue  # more lines of this block follow
                    if line.startswith("7"):
                        if self._sock is sock:  # a dead connection's events never touch the new one's state
                            self._event(block)
                    else:
                        replies.put(block)
                    block = []
        except OSError:
            pass
        if self._sock is sock:
            self._drop()

    def _event(self, block: list[str]) -> None:
        code = block[-1][:3]
        msg_id = block[0][4:] if len(block) > 1 else ""
        if code == "701":  # BEGIN
            self._playing.set()
        elif code in ("702", "703"):  # END, CANCELED
            with self._state:
                self._queued.discard(msg_id)
                if not self._queued:
                    self._playing.clear()

    # Speaker protocol

    def available(self) -> bool:
        return self._ensure() is not None

    def speak(self, text: str, interrupt: bool, priority: Priority | None = None) -> None:
        if self._ensure() is None:
            return
        # SSIP data: one line per line; a line that starts with "." gets a second "." (dot-stuffing).
        lines = [("." + line) if line.startswith(".") else line for line in text.replace("\r", "").split("\n")]
        level = SSIP_PRIORITY.get(priority or ("high" if interrupt else "normal"), "text")
        try:
            with self._send_lock:
                if interrupt:
                    self._send("CANCEL self")  # only Artemis's own messages; never Orca's
                    self._reply()
                    with self._state:
                        self._queued.clear()
                    self._playing.clear()
                if level != self._level:  # one round trip only when the level changes
                    self._send(f"SET self PRIORITY {level}")
                    self._reply()
                    self._level = level
                self._send("SPEAK")
                self._reply()  # 230 OK RECEIVING DATA
                self._send(*lines, ".")
                queued = self._reply()  # 225-<msg id> / 225 OK MESSAGE QUEUED
            # A notification can be discarded silently, with no END or CANCELED event, so it is never
            # counted: otherwise busy() would stay true for the session and every later low line be lost.
            if len(queued) > 1 and level != "notification":
                with self._state:
                    self._queued.add(queued[0][4:])
        except Exception as e:
            log.warning("speech-dispatcher failed to speak: %s", type(e).__name__)
            self._drop()

    def cancel(self) -> None:
        if self._sock is None:
            return
        try:
            self._command("CANCEL self")
        except Exception:
            self._drop()
            return
        with self._state:
            self._queued.clear()
        self._playing.clear()

    def busy(self) -> bool:
        with self._state:
            return bool(self._queued)

    def speaking(self) -> bool:
        """Audio is playing now: between speech-dispatcher's BEGIN and END events for our message."""
        return self._playing.is_set()


def _speechd_connect():
    """Connect to speech-dispatcher's socket, starting it once (argv only) if it isn't running yet."""
    import socket

    address = os.environ.get("SPEECHD_ADDRESS", "")
    if address.startswith("unix_socket:"):
        path = Path(address.split(":", 1)[1])
    else:
        runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        path = Path(runtime) / "speech-dispatcher" / "speechd.sock"
    if not path.exists():
        spawn = shutil.which("speech-dispatcher")
        if spawn is None:
            raise FileNotFoundError("speech-dispatcher")
        subprocess.run([spawn, "--spawn"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=5, check=False)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(None)
    sock.connect(str(path))
    return sock


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
        # The local voice: SAPI on Windows, speech-dispatcher elsewhere. NVDA only ever runs on Windows
        # (nvda_running() is False elsewhere), so on Linux the order is ElevenLabs, offline, silence.
        self.sapi = sapi or (SapiSpeaker() if sys.platform == "win32" else SpeechDispatcherSpeaker())
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

    def speaking_engine(self) -> str | None:
        """Which speaker is audibly saying Artemis's line now, or None. ElevenLabs: its audio is playing.
        SAPI: SpVoice is speaking. NVDA: only Artemis's own estimate, since NVDA can't report playback."""
        eleven = self.eleven
        if eleven is not None and getattr(eleven, "speaking", eleven.busy)():
            return "elevenlabs"
        for speaker in (self.sapi, self.nvda):
            try:
                if speaker.available() and speaker.busy():
                    return speaker.name
            except Exception:  # a speaker that can't be asked is not speaking
                continue
        return None

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
        if isinstance(speaker, (SpeechDispatcherSpeaker, ElevenLabsSpeaker)):
            # speechd: the level decides who may cut whom; no probe of other speakers. ElevenLabs: only high
            # cuts the line playing, normal queues, and a newer line drops a progress line not heard yet.
            speaker.speak(text, interrupt=priority == "high", priority=priority)
        else:  # NVDA, SAPI: unchanged
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
