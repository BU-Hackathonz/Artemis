"""WhisperVoice: contracts.md 3.3 Voice with a real microphone.

Push-to-talk: the client calls start on key down and stop on key up. sounddevice records 16 kHz
mono into memory; faster-whisper transcribes locally on the CPU. Audio is never written to disk
and is dropped as soon as it is transcribed (handoff: minimal audio retention).

Manual check on a laptop: python -m artemis.voice.listen  (records 4 s, prints the transcript)
"""

from __future__ import annotations

import asyncio
import threading

import numpy as np

from .speech import Announcer, Priority
from .text import strip_wake_word, vocabulary_prompt

RATE = 16_000
MAX_SECONDS = 60  # a stuck key can't fill memory
MIN_SECONDS = 0.3  # shorter is a key tap, not speech


class VoiceError(Exception):
    """Microphone problems the core should turn into an `error` event."""


def _input_stream(callback):
    import sounddevice as sd

    return sd.InputStream(samplerate=RATE, channels=1, dtype="float32", callback=callback)


class WhisperVoice:
    def __init__(self, announcer: Announcer | None = None, model_name: str = "small.en", model=None, stream_factory=None) -> None:
        self.announcer = announcer or Announcer()
        self.model_name = model_name
        self._model = model
        self._stream_factory = stream_factory or _input_stream
        self._stream = None
        self._chunks: list[np.ndarray] = []
        self._frames = 0
        self._lock = threading.Lock()  # the audio callback runs on PortAudio's thread
        self.prompt = ""

    @property
    def listening(self) -> bool:
        return self._stream is not None

    def warm(self) -> None:
        """Load the model now (about 3 s, longer on first download) instead of on the first request."""
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")

    def _on_audio(self, data, frames, time, status) -> None:
        with self._lock:
            if self._frames < RATE * MAX_SECONDS:
                self._chunks.append(data[:, 0].copy())
                self._frames += frames

    async def start_listening(self) -> None:
        if self._stream is not None:
            return  # key repeat while held
        with self._lock:
            self._chunks, self._frames = [], 0
        try:
            stream = self._stream_factory(self._on_audio)
            stream.start()
        except Exception as e:  # no device, device busy, permission denied
            raise VoiceError(f"Microphone not available: {e}") from e
        self._stream = stream

    async def stop_listening(self, client_request_id: str) -> str:
        if self._stream is None:
            return ""
        stream, self._stream = self._stream, None
        try:
            stream.stop()
            stream.close()
        except Exception as e:  # mic unplugged mid-recording
            with self._lock:
                self._chunks, self._frames = [], 0
            raise VoiceError(f"Microphone stopped unexpectedly: {e}") from e
        with self._lock:
            audio = np.concatenate(self._chunks)[: RATE * MAX_SECONDS] if self._chunks else np.zeros(0, np.float32)
            self._chunks, self._frames = [], 0
        if len(audio) < RATE * MIN_SECONDS:
            return ""
        text = await asyncio.to_thread(self._transcribe, audio)
        return strip_wake_word(text)

    def _transcribe(self, audio: np.ndarray) -> str:
        self.warm()
        # Pass numpy, not a file: faster-whisper's file decoder breaks on current PyAV.
        segments, _ = self._model.transcribe(
            audio, language="en", beam_size=1, vad_filter=True, initial_prompt=self.prompt or None
        )
        return " ".join(s.text.strip() for s in segments).strip()

    def set_vocabulary(self, words: list[str]) -> None:
        self.prompt = vocabulary_prompt(words)

    async def announce(self, text: str, priority: Priority) -> None:
        await self.announcer.announce(text, priority)

    async def stop_speech(self) -> None:
        await self.announcer.stop_speech()

    async def repeat_last(self) -> None:
        await self.announcer.repeat_last()


if __name__ == "__main__":
    async def _demo() -> None:
        v = WhisperVoice()
        v.warm()
        await v.announce("Speak now for four seconds.", "high")
        await asyncio.sleep(2)  # let the prompt finish so the mic doesn't hear it
        await v.start_listening()
        await asyncio.sleep(4)
        text = await v.stop_listening("cr_demo")
        print(repr(text))
        await v.announce(f"Heard: {text}" if text else "I didn't hear anything.", "high")

    asyncio.run(_demo())
