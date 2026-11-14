"""WhisperVoice: contracts.md 3.3 Voice with a real microphone.

Push-to-talk: the client calls start on key down and stop on key up. Start stops current speech,
plays the "listening" earcon to the end, and only then opens the mic, so neither Artemis's voice
nor the beep is transcribed (contracts.md 5.1). sounddevice records 16 kHz
mono into memory; faster-whisper transcribes locally on the CPU. Audio is never written to disk
and is dropped as soon as it is transcribed (handoff: minimal audio retention).

Manual check on a laptop: python -m artemis.voice.listen  (records 4 s, prints the transcript)
"""

from __future__ import annotations

import asyncio
import threading

import numpy as np

from . import earcons
from .speech import Announcer, Priority
from .text import strip_wake_word, vocabulary_prompt

RATE = 16_000
MAX_SECONDS = 60  # a stuck key can't fill memory
MIN_SECONDS = 0.3  # shorter is a key tap, not speech
# Silence must stay silent: Whisper invents text ("Thank you.", "and still") from quiet room noise.
# Measured on a laptop headset mic: room silence RMS 0.00002-0.00022; speech is far louder.
MIN_RMS = 0.001  # if even the loudest 100 ms is below this, nobody spoke: skip Whisper
WINDOW = RATE // 10  # 100 ms: a short word in a long key hold is judged on its own, not diluted
# Drop segments Whisper itself thinks are probably not speech. Measured: the fake "Thank you." scored
# 0.70; real short commands (cancel, stop, repeat, yes, no...) scored 0.07 or less. avg_logprob does not
# separate them (commands -0.41 to -0.93, the fake -0.95), so it is deliberately not used.
MAX_NO_SPEECH = 0.6


def loudest_rms(audio: np.ndarray) -> float:
    """RMS of the loudest 100 ms window."""
    n = len(audio) // WINDOW
    if n == 0:
        return float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
    frames = audio[: n * WINDOW].reshape(n, WINDOW)
    return float(np.sqrt(np.mean(np.square(frames), axis=1)).max())


class VoiceError(Exception):
    """Microphone problems the core should turn into an `error` event."""


def _input_stream(callback):
    import sounddevice as sd

    return sd.InputStream(samplerate=RATE, channels=1, dtype="float32", callback=callback)


class WhisperVoice:
    def __init__(self, announcer: Announcer | None = None, model_name: str = "small.en", model=None, stream_factory=None, earcon=earcons.play) -> None:
        self.announcer = announcer or Announcer()
        self.model_name = model_name
        self._model = model
        self._stream_factory = stream_factory or _input_stream
        self._earcon = earcon
        self._stream = None
        self._wanted = False  # key is held; cleared by stop, even while the start earcon plays
        self._gen = 0  # bumped by every press and release, so a stale start never opens the mic
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
        if self._wanted:
            return  # key repeat while held
        self._wanted = True
        self._gen += 1
        gen = self._gen
        await self.announcer.stop_speech()
        await asyncio.sleep(self._earcon("listening"))  # let the beep finish before the mic opens
        if not self._wanted or gen != self._gen:
            return  # key released (and maybe pressed again) during the earcon: a newer press owns the mic
        with self._lock:
            self._chunks, self._frames = [], 0
        try:
            stream = self._stream_factory(self._on_audio)
            stream.start()
        except Exception as e:  # no device, device busy, permission denied
            self._wanted = False
            raise VoiceError(f"Microphone not available: {e}") from e
        self._stream = stream

    async def stop_listening(self, client_request_id: str) -> str:
        self._wanted = False
        self._gen += 1
        if self._stream is None:
            return ""
        stream, self._stream = self._stream, None
        self._earcon("stopped")  # mic is about to close; no need to wait
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
        if loudest_rms(audio) < MIN_RMS:
            return ""  # nobody spoke
        text = await asyncio.to_thread(self._transcribe, audio)
        return strip_wake_word(text)

    def _transcribe(self, audio: np.ndarray) -> str:
        self.warm()
        # Pass numpy, not a file: faster-whisper's file decoder breaks on current PyAV.
        segments, _ = self._model.transcribe(
            audio, language="en", beam_size=1, vad_filter=True, initial_prompt=self.prompt or None
        )
        kept = [s for s in segments if getattr(s, "no_speech_prob", 0.0) <= MAX_NO_SPEECH]
        return " ".join(s.text.strip() for s in kept).strip()

    def set_vocabulary(self, words: list[str]) -> None:
        self.prompt = vocabulary_prompt(words)

    async def announce(self, text: str, priority: Priority) -> None:
        await self.announcer.announce(text, priority)

    async def stop_speech(self) -> None:
        await self.announcer.stop_speech()

    async def repeat_last(self) -> None:
        await self.announcer.repeat_last()

    async def set_rate(self, delta: int) -> bool:
        return await self.announcer.set_rate(delta)


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
