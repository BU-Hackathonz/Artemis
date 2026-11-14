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


class Speaker(Protocol):
    name: str

    def available(self) -> bool: ...
    def speak(self, text: str, interrupt: bool) -> None: ...
    def cancel(self) -> None: ...
    def busy(self) -> bool: ...


class NvdaSpeaker:
    """NVDA Controller Client: speaks in the user's own NVDA voice and rate, from any focused app."""

    name = "nvda"

    def __init__(self, dll_path: Path = NVDA_DLL) -> None:
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
        if interrupt:
            self._dll.nvdaController_cancelSpeech()
        self._dll.nvdaController_speakText(text)
        self._dll.nvdaController_brailleMessage(text)

    def cancel(self) -> None:
        self._dll.nvdaController_cancelSpeech()

    def busy(self) -> bool:
        return False  # the controller API can't tell; low-priority text is spoken anyway


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

    async def set_rate(self, delta: int) -> bool:
        """'Slower' / 'faster' from the conversation controller. Speaks its own confirmation.

        Only SAPI's rate is ours to change. Under NVDA the voice and rate are the user's own settings,
        so they are never touched. Returns True when the rate changed.
        """
        speaker = self.current()
        if speaker.name == "nvda":
            await self.announce("I'm using your NVDA voice. Change its speed in NVDA's speech settings.", "normal")
            return False
        if not hasattr(speaker, "set_rate"):
            return False  # no voice output at all; there is nothing to make faster or slower
        if not delta:
            return False
        step = 1 if delta > 0 else -1
        before = speaker.rate
        # The user's own Windows rate can sit outside our range (we keep it unless asked). Stepping
        # from there must never go the wrong way: "faster" from 8 would clamp down to 6.
        at_limit = before >= RATE_MAX if step > 0 else before <= RATE_MIN
        after = before if at_limit else speaker.set_rate(before + step)
        if after == before:
            await self.announce("That's the fastest I go." if delta > 0 else "That's the slowest I go.", "normal")
            return False
        await self.announce("Speaking faster." if delta > 0 else "Speaking slower.", "normal")
        return True
