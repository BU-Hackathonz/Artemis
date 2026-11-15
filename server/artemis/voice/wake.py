"""Hands-free "Hey Artemis": listen for the wake phrase, then take the next words as a request.

Opt-in with ARTEMIS_LISTEN_MODE=wake (push-to-talk stays the default and keeps working alongside).
Ported from tools/wake_prototype (Yaovi's prototype, 14d7728); the pure parts are unchanged:

- `Segmenter` cuts the microphone stream into utterances by loudness.
- `wake_request` decides from a transcript whether it began with "Hey Artemis".
- `WakeLoop` joins them: frames in, events out.
- `WakeListener` runs that on the live microphone with the same Whisper model as push-to-talk and
  hands a finished request to the core, which treats it exactly like a push-to-talk transcript.

Safety: a request only starts a task, the same as typing it. Every write and run still needs a
keyboard approval. Speech that does not start with the wake phrase is never kept: only its word
count is logged. Audio lives in memory until it is transcribed and is then dropped.

Artemis must not hear itself: the microphone's frames are dropped unheard while any of Artemis's
speakers is busy (ElevenLabs downloading or playing, SAPI speaking, Artemis's NVDA estimate), for a
short tail after it, and while push-to-talk owns the microphone. NVDA reading the screen is not
visible to Artemis, so with NVDA through the laptop speakers use a headset (README, wake word).
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

RATE = 16_000
FRAME = RATE // 10  # 100 ms

# ----- cutting the stream into utterances -----


def rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(frame)))) if len(frame) else 0.0


class Segmenter:
    """Loudness-based endpointing.

    Speech starts when a frame is clearly above the room's noise floor and ends after
    `end_silence_s` of quiet. The noise floor follows the room, so steady background noise
    raises the bar instead of holding the microphone open forever. A cap (`max_s`) ends an
    utterance that never goes quiet, which is what happens when people nearby keep talking.
    """

    def __init__(
        self,
        *,
        # Never treat anything quieter than this as speech. Measured on Yaovi's EarPods mic (2026-11-15):
        # room 0.0001-0.0008, normal speech 0.0017-0.0046, so 0.004 missed most of it. The floor * over_floor
        # rule still lifts the bar in a noisy room.
        min_rms: float = 0.0015,
        over_floor: float = 3.0,  # speech must be this many times the noise floor
        end_silence_s: float = 0.8,
        min_s: float = 0.4,  # shorter is a click or a cough
        max_s: float = 12.0,
        pre_roll_s: float = 0.3,  # kept from before the start, so the "H" of "Hey" isn't lost
        start_rms: float = 0.004,  # the stricter bar until the room is known, so steady noise is learned, not heard
        learn_s: float = 1.0,  # quiet heard before min_rms applies
    ) -> None:
        self.min_rms = min_rms
        self.over_floor = over_floor
        self.end_frames = max(1, round(end_silence_s * RATE / FRAME))
        self.min_frames = max(1, round(min_s * RATE / FRAME))
        self.max_frames = max(1, round(max_s * RATE / FRAME))
        self.floor = 0.0
        self.start_rms = start_rms
        self._to_learn = max(0, round(learn_s * RATE / FRAME))
        self._pre: deque[np.ndarray] = deque(maxlen=max(1, round(pre_roll_s * RATE / FRAME)))
        self._chunks: list[np.ndarray] = []
        self._voiced = 0
        self._quiet = 0
        self.capped = False  # the last utterance ended because of max_s, not silence

    @property
    def threshold(self) -> float:
        bar = self.start_rms if self._to_learn else self.min_rms
        return max(bar, self.floor * self.over_floor)

    @property
    def in_speech(self) -> bool:
        return bool(self._chunks)

    def reset(self) -> None:
        """Drop whatever was being collected (used while Artemis itself is speaking)."""
        self._chunks, self._voiced, self._quiet = [], 0, 0
        self._pre.clear()

    def feed(self, frame: np.ndarray) -> np.ndarray | None:
        """Give it one 100 ms frame. Returns a finished utterance, or None."""
        level = rms(frame)
        loud = level >= self.threshold
        if not self._chunks:
            if not loud:
                # Quiet and not in speech: this is what the room sounds like.
                self.floor = level if self.floor == 0.0 else 0.95 * self.floor + 0.05 * level
                self._to_learn = max(0, self._to_learn - 1)
                self._pre.append(frame)
                return None
            self._chunks = [*self._pre, frame]
            self._pre.clear()
            self._voiced, self._quiet = 1, 0
            return None

        self._chunks.append(frame)
        if loud:
            self._voiced += 1
            self._quiet = 0
        else:
            self._quiet += 1
        ended = self._quiet >= self.end_frames
        capped = len(self._chunks) >= self.max_frames
        if not (ended or capped):
            return None
        chunks, voiced = self._chunks, self._voiced
        self._chunks, self._voiced, self._quiet = [], 0, 0
        self.capped = capped and not ended
        if voiced < self.min_frames:
            return None
        return np.concatenate(chunks)


# ----- recognizing the phrase in a transcript -----

# Whisper spells the name several ways. A greeting word is required: a bare "Artemis" turns up
# whenever people talk *about* Artemis, and must not wake it.
_NAME = r"art[ae]m[iaeu]s|artimis|artimus|artemas|artemiss|artemise"
_WAKE = re.compile(rf"^\W*(?:hey|hay|hi|hello|okay|ok)\W+(?:{_NAME})\b[\s,.:;!?-]*", re.IGNORECASE)


def wake_request(transcript: str) -> str | None:
    """None if the transcript does not start with the wake phrase. Otherwise the rest of it,
    untouched: "" when they only said "Hey Artemis"."""
    found = _WAKE.match(transcript)
    if found is None:
        return None
    return transcript[found.end():].strip()


# ----- the loop -----


@dataclass(frozen=True)
class Event:
    kind: str  # "wake", "request", "ignored", "suppressed", "timeout"
    text: str = ""
    seconds: float = 0.0  # length of the utterance


@dataclass
class Stats:
    utterances: int = 0
    wakes: int = 0
    requests: int = 0
    ignored: int = 0  # speech that did not start with the wake phrase
    suppressed: int = 0  # frames dropped because Artemis was speaking
    capped: int = 0  # utterances cut at max_s because the room never went quiet
    events: list[Event] = field(default_factory=list)


class WakeLoop:
    """Frames in, events out.

    - "Hey Artemis, do X" in one breath gives `request` with "do X".
    - "Hey Artemis" on its own gives `wake`; the next utterance within `window_s` is the
      request, with no need to say the phrase again.
    - Anything else is `ignored`: its text is not kept (only the word count), because it is
      other people's conversation.
    - While `speaking()` is true, audio is dropped unheard, so Artemis can't wake itself.
    """

    def __init__(
        self,
        transcribe: Callable[[np.ndarray], str],
        *,
        speaking: Callable[[], bool] = lambda: False,
        segmenter: Segmenter | None = None,
        window_s: float = 8.0,
        tail_s: float = 0.4,  # keep ignoring this long after Artemis stops speaking (room echo)
    ) -> None:
        self.transcribe = transcribe
        self.speaking = speaking
        self.segmenter = segmenter or Segmenter()
        self.window_frames = round(window_s * RATE / FRAME)
        self.tail_frames = round(tail_s * RATE / FRAME)
        self.stats = Stats()
        self._armed = 0  # frames left in which a request needs no wake phrase
        self._tail = 0

    @property
    def armed(self) -> bool:
        return self._armed > 0

    def feed(self, frame: np.ndarray) -> Event | None:
        talking = self.speaking()
        if talking:
            self._tail = self.tail_frames
        if talking or self._tail > 0:
            if not talking:
                self._tail -= 1
            self.segmenter.reset()
            self.stats.suppressed += 1
            return None

        was_armed = self.armed
        if self._armed and not self.segmenter.in_speech:
            self._armed -= 1
            if self._armed == 0:
                return self._emit(Event("timeout"))

        audio = self.segmenter.feed(frame)
        if audio is None:
            return None
        self.stats.utterances += 1
        if self.segmenter.capped:
            self.stats.capped += 1
        seconds = len(audio) / RATE
        text = self.transcribe(audio).strip()
        del audio  # nothing is kept once it has been transcribed
        if not text:
            return None

        request = wake_request(text)
        if request is not None:
            self.stats.wakes += 1
        elif was_armed:
            request = text  # inside the window after "Hey Artemis": no phrase needed
        else:
            self.stats.ignored += 1
            return self._emit(Event("ignored", f"{len(text.split())} words", seconds))
        if request == "":
            self._armed = self.window_frames
            return self._emit(Event("wake", seconds=seconds))
        self.stats.requests += 1
        self._armed = 0
        return self._emit(Event("request", request, seconds))

    def _emit(self, event: Event) -> Event:
        self.stats.events.append(event)
        return event


# ----- the live microphone (opt-in: ARTEMIS_LISTEN_MODE=wake) -----

import asyncio  # noqa: E402  (kept with the live part; the pure part above needs none of these)
import logging  # noqa: E402
import os  # noqa: E402
import queue  # noqa: E402
import threading  # noqa: E402

log = logging.getLogger(__name__)


def listen_mode() -> str:
    """ARTEMIS_LISTEN_MODE: "wake" turns on "Hey Artemis"; anything else (the default) is push-to-talk only."""
    return "wake" if os.environ.get("ARTEMIS_LISTEN_MODE", "").strip().lower() == "wake" else "push-to-talk"


def _frame_stream(callback):
    import sounddevice as sd

    return sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=FRAME, callback=callback)


class WakeListener:
    """The wake loop on the live microphone, on a worker thread.

    `on_request(text)` is called on the event loop with the words after "Hey Artemis". `armed` is
    true from a bare "Hey Artemis" until the request or the window's end: that is when Artemis is
    honestly listening for a request, and voice.activity reports it as `listening`.
    """

    def __init__(self, voice, on_request: Callable[[str], object], *, stream_factory=None,
                 loop_factory: Callable[..., WakeLoop] | None = None) -> None:
        self.voice = voice
        self.on_request = on_request
        self._stream_factory = stream_factory or _frame_stream
        self._frames: queue.Queue = queue.Queue(maxsize=600)  # about a minute; full means the worker is stuck
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream = None
        self._loop: asyncio.AbstractEventLoop | None = None
        make = loop_factory or WakeLoop
        self.wake = make(self._transcribe, speaking=self._muted)
        # Health counters for the periodic core.log line (numbers only: no audio, no words).
        self._health = {"frames": 0, "peak": 0.0, "muted": {}}
        self._health_at = time.monotonic()

    @property
    def armed(self) -> bool:
        return self.wake.armed

    def _muted(self) -> bool:
        """Drop the microphone's audio: Artemis is talking, or push-to-talk owns the mic."""
        reason = self._mute_reason()
        if reason is not None:
            counts = self._health["muted"]
            counts[reason] = counts.get(reason, 0) + 1
        return reason is not None

    def _mute_reason(self) -> str | None:
        voice = self.voice
        if getattr(voice, "busy_listening", getattr(voice, "listening", False)):
            return "push-to-talk"
        if getattr(voice, "transcribing", False):
            return "transcribing"
        announcer = getattr(voice, "announcer", None)
        if announcer is None:
            return None
        try:
            if announcer.speaking_engine() is not None:
                return "speaking"
            if announcer.current().busy():
                return "speech queued"  # a queued or downloading line is about to be heard
            return None
        except Exception:
            return "speech unknown"  # can't tell: safer not to listen than to hear ourselves

    def _transcribe(self, audio):
        return self.voice._transcribe(audio)

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stream = self._stream_factory(self._on_audio)
        self._stream.start()
        self._thread = threading.Thread(target=self._run, name="artemis-wake", daemon=True)
        self._thread.start()
        log.warning("Wake word on: say \"Hey Artemis\" and the request")  # warning so core.log shows it

    def stop(self) -> None:
        self._stop.set()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _on_audio(self, data, frames, when, status) -> None:
        try:
            self._frames.put_nowait(data[:, 0].copy() if getattr(data, "ndim", 1) == 2 else data.copy())
        except queue.Full:
            pass  # the worker is behind (a slow transcription): dropping beats unbounded memory

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self._frames.get(timeout=0.2)
            except queue.Empty:
                continue
            self._note(frame)
            try:
                event = self.wake.feed(frame)
            except Exception as e:  # one bad utterance must not end hands-free listening
                log.warning("Wake word: an utterance failed (%s)", type(e).__name__)
                continue
            if event is None:
                continue
            self._handle(event)

    HEALTH_S = 30.0  # how often core.log gets the wake word's health line

    def _note(self, frame) -> None:
        """Count the frame, and every HEALTH_S log one line saying whether the wake word can hear: the mic's
        loudest level, how many frames were muted and why, and the loop's counts. Numbers only."""
        h = self._health
        h["frames"] += 1
        h["peak"] = max(h["peak"], rms(frame))
        now = time.monotonic()
        if now - self._health_at < self.HEALTH_S:
            return
        stats = getattr(self.wake, "stats", None)
        muted = ", ".join(f"{k} {v}" for k, v in sorted(h["muted"].items())) or "none"
        log.warning("Wake word: %d frames in %.0f s, loudest %.4f (speech needs about %.4f), muted: %s; "
                    "utterances %d, wakes %d, ignored %d",
                    h["frames"], now - self._health_at, h["peak"], getattr(getattr(self.wake, "segmenter", None), "threshold", 0.0),
                    muted, getattr(stats, "utterances", 0), getattr(stats, "wakes", 0), getattr(stats, "ignored", 0))
        self._health = {"frames": 0, "peak": 0.0, "muted": {}}
        self._health_at = now

    def _handle(self, event: Event) -> None:
        if event.kind == "wake":
            self._earcon("listening")
        elif event.kind == "request":
            self._earcon("stopped")
            if self._loop is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self.on_request, event.text)
        elif event.kind == "ignored":
            log.info("Wake word: ignored speech (%s)", event.text)  # a word count, never the words
        elif event.kind == "timeout":
            self._earcon("stopped")

    def _earcon(self, name: str) -> None:
        play = getattr(self.voice, "_earcon", None)
        if play is not None:
            try:
                play(name)
            except Exception:
                pass
