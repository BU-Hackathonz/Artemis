"""StubVoice: contracts.md 3.3 Voice without a microphone, for the Phase 1 walking skeleton.

Speech is real (Announcer); listening returns a canned transcript.
"""

from __future__ import annotations

from .speech import Announcer, Priority, RateStatus
from .text import strip_wake_word, vocabulary_prompt

MVP_REQUEST = "Artemis, create a Python program that prints Hello World"


class StubVoice:
    def __init__(self, transcript: str = MVP_REQUEST, announcer: Announcer | None = None) -> None:
        self.transcript = transcript
        self.announcer = announcer or Announcer()
        self.prompt = ""
        self.listening = False

    async def start_listening(self) -> None:
        await self.announcer.stop_speech()  # contracts.md 5.1, same as WhisperVoice
        self.listening = True

    async def stop_listening(self, client_request_id: str) -> str:
        self.listening = False
        return strip_wake_word(self.transcript)

    def set_vocabulary(self, words: list[str]) -> None:
        self.prompt = vocabulary_prompt(words)

    async def announce(self, text: str, priority: Priority) -> None:
        await self.announcer.announce(text, priority)

    async def stop_speech(self) -> None:
        await self.announcer.stop_speech()

    async def repeat_last(self) -> None:
        await self.announcer.repeat_last()

    async def set_rate(self, delta: int) -> RateStatus:
        return await self.announcer.set_rate(delta)
