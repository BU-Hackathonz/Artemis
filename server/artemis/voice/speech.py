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
import os
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol

from artemis.contracts.events import Priority

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
    # 3.5 words a second is about NVDA's default rate. For a faster user a low-priority progress line
    # may be dropped a little early; a normal or high line is never dropped because of busy().
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
                log.warning("Could not set the SAPI rate: %s", e)

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


class Announcer:
    """announce() for the core: picks NVDA, SAPI or silence, applies priority, remembers the last line."""

    def __init__(self, nvda: Speaker | None = None, sapi: Speaker | None = None, is_nvda_running: Callable[[], bool] = nvda_running) -> None:
        self.nvda = nvda or NvdaSpeaker()
        self.sapi = sapi or SapiSpeaker()
        self.is_nvda_running = is_nvda_running
        self.last: str | None = None
        self._warned = False

    def current(self) -> Speaker:
        if self.is_nvda_running():
            if self.nvda.available():
                return self.nvda
            if not self._warned:  # never let SAPI talk over a running NVDA
                log.warning("NVDA is running but its controller DLL is unavailable; staying silent. Run: python -m artemis.voice.setup")
                self._warned = True
            return NullSpeaker()
        return self.sapi if self.sapi.available() else NullSpeaker()

    def status(self) -> tuple[bool, str | None]:
        """(voice_ready, screen_reader) as contracts.md voice.status and /health report them."""
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
