"""Spoken output: NVDA's own voice when NVDA runs, Windows SAPI otherwise, never both.

The router checks NVDA on every call, so starting NVDA after the core still stops SAPI
from talking over it (architecture.md risk 3).
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import Literal, Protocol

# shortcut: mirrors contracts.md Priority; import it from artemis.contracts once that package lands.
Priority = Literal["low", "normal", "high"]

# The DLL ships in NVDA's controllerClient zip; it is not committed. Only these two paths are tried.
NVDA_DLL = Path(os.environ.get("ARTEMIS_NVDA_DLL") or Path(__file__).parent / "bin" / "nvdaControllerClient.dll")


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
            except (OSError, AttributeError):
                self._dll = None

    def available(self) -> bool:
        # testIfRunning returns 0 when NVDA is running.
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
    """Windows SAPI 5 through COM. Asynchronous, so the event loop never blocks on speech."""

    name = "sapi"
    _ASYNC, _PURGE = 1, 2  # SVSFlagsAsync, SVSFPurgeBeforeSpeak
    _SPEAKING = 2  # SRSEIsSpeaking

    def __init__(self) -> None:
        self._voice = None
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            self._voice = win32com.client.Dispatch("SAPI.SpVoice")
        except Exception:  # pywin32 missing or COM unavailable: report unavailable, don't crash the core
            self._voice = None

    def available(self) -> bool:
        return self._voice is not None

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
    """announce() for the core: routes to the first available speaker, applies priority, remembers the last line."""

    def __init__(self, speakers: list[Speaker] | None = None) -> None:
        self.speakers = speakers if speakers is not None else [NvdaSpeaker(), SapiSpeaker()]
        self.last: str | None = None

    def current(self) -> Speaker:
        return next((s for s in self.speakers if s.available()), NullSpeaker())

    @property
    def voice_ready(self) -> bool:
        return self.current().name != "none"

    @property
    def screen_reader(self) -> str | None:
        return "nvda" if self.current().name == "nvda" else None

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
