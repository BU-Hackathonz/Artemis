"""Hands-free "Hey Artemis": listen for the wake phrase, then take the next words as a request.

Opt-in with ARTEMIS_LISTEN_MODE=wake (push-to-talk stays the default and keeps working alongside).
Ported from tools/wake_prototype (Yaovi's prototype, 14d7728); the pure parts are unchanged:

- `Segmenter` cuts the microphone stream into utterances: by voice when a `VoiceDetector` (Silero VAD from
  faster-whisper) scores each frame, so fans, hiss and music don't hold the mic open; by loudness otherwise.
- `denoise` cleans each utterance before Whisper (the room's steady noise, rumble and hum removed).
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
        self.started = 0  # utterances begun so far: tells one utterance from the next
        self.last_voiced = 0  # voiced frames in the utterance feed() last returned
        self.capped = False  # the last utterance ended because of max_s, not silence

    @property
    def threshold(self) -> float:
        bar = self.start_rms if self._to_learn else self.min_rms
        return max(bar, self.floor * self.over_floor)

    @property
    def in_speech(self) -> bool:
        return bool(self._chunks)

    @property
    def voiced(self) -> int:
        return self._voiced

    @property
    def quiet_frames(self) -> int:
        """Quiet frames since the last voiced one, in the utterance being collected."""
        return self._quiet

    def so_far(self) -> np.ndarray:
        """The utterance being collected, as it is now (a copy; collecting goes on)."""
        return np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)

    def reset(self) -> None:
        """Drop whatever was being collected (used while Artemis itself is speaking)."""
        self._chunks, self._voiced, self._quiet = [], 0, 0
        self._pre.clear()

    def feed(self, frame: np.ndarray, voice: float | None = None) -> np.ndarray | None:
        """Give it one 100 ms frame. Returns a finished utterance, or None.

        `voice` is a voice detector's speech probability for the frame (VoiceDetector). With it, speech
        is what sounds like a voice, not whatever is loud: a fan, hiss or music no longer holds the
        utterance open. Starting needs a clear voice and staying in speech needs less (hysteresis), so
        soft word endings are not chopped. Without it, loudness decides, as before."""
        level = rms(frame)
        if voice is None:
            loud = level >= self.threshold
        else:
            loud = voice > (VOICE_STAY if self._chunks else VOICE_START) and level >= self.min_rms * 0.5
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
            self.started += 1
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
        self.last_voiced = voiced
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


# ----- hearing the speaker, not the room -----

VOICE_START = 0.55  # speech probability that starts an utterance
VOICE_STAY = 0.35   # and that keeps it going

# Short stock phrases a small Whisper model produces from noise or silence. Never a request.
HALLUCINATIONS = {"you", "thank you", "thanks for watching", "thank you for watching", "bye", "so"}


class VoiceDetector:
    """Speech probability per 100 ms frame from Silero VAD, which ships with faster-whisper (no new
    dependency). The model keeps no state between calls, so each frame is scored with the last second of
    audio around it; that costs about 2 ms a frame on a laptop CPU."""

    WINDOW = 512 * 31  # Silero scores 512-sample chunks; about a second of them

    def __init__(self, model) -> None:
        self.model = model
        self._ring = np.zeros(self.WINDOW, np.float32)

    @classmethod
    def load(cls) -> VoiceDetector | None:
        try:
            from faster_whisper.vad import get_vad_model
            return cls(get_vad_model())
        except Exception:
            return None  # no voice group installed: loudness endpointing, as before

    def __call__(self, frame: np.ndarray) -> float:
        frame = np.asarray(frame, np.float32)
        self._ring = np.concatenate([self._ring[len(frame):], frame])
        return float(np.ravel(self.model(self._ring))[-3:].max())


def denoise(audio: np.ndarray, noise: np.ndarray) -> np.ndarray:
    """Clean an utterance before Whisper hears it, with numpy only: the room's steady noise (measured from
    `noise`, recent audio with nobody talking) is subtracted spectrally, everything under 80 Hz (rumble,
    desk thumps, mains hum) is removed, and the level is evened out so a quiet speaker reads normally."""
    n, hop = 512, 128
    audio = np.asarray(audio, np.float32)
    if len(audio) < n or len(noise) < n:
        return audio
    # high-pass on the whole utterance (precise at 60 Hz, where 512-sample frames are too coarse): 0 below
    # 80 Hz, a short ramp to 120 Hz, the voice untouched
    whole = np.fft.rfft(audio)
    f = np.fft.rfftfreq(len(audio), 1 / RATE)
    whole *= np.clip((f - 80) / 40, 0.0, 1.0)
    audio = np.fft.irfft(whole, n=len(audio)).astype(np.float32)
    win = np.hanning(n).astype(np.float32)

    def frames(x):
        count = 1 + (len(x) - n) // hop
        return np.stack([x[i * hop:i * hop + n] * win for i in range(count)])

    spec = np.fft.rfft(frames(audio), axis=1)
    floor = np.abs(np.fft.rfft(frames(np.asarray(noise, np.float32)), axis=1)).mean(axis=0)
    mag = np.abs(spec)
    gain = np.clip(1.0 - 1.5 * floor / np.maximum(mag, 1e-9), 0.1, 1.0)  # keep a little: no "musical noise"
    out_frames = np.fft.irfft(spec * gain, n=n, axis=1) * win
    out = np.zeros(len(audio), np.float32)
    norm = np.zeros(len(audio), np.float32)
    for i, f in enumerate(out_frames):
        out[i * hop:i * hop + n] += f
        norm[i * hop:i * hop + n] += win * win
    out = out / np.maximum(norm, 1e-6)
    tail = (len(out_frames) - 1) * hop + n
    out[tail:] = audio[tail:]
    peak = float(np.max(np.abs(out))) or 1.0
    return (out / peak * 0.9).astype(np.float32)


# ----- the loop -----


@dataclass(frozen=True)
class Event:
    kind: str  # "wake", "request", "ignored", "suppressed", "timeout", "misheard" (a quick wake taken back)
    text: str = ""
    seconds: float = 0.0  # length of the utterance
    quiet_s: float = 0.0  # quiet after the last word before it was decided (the end silence)
    whisper_s: float = 0.0  # time spent transcribing it


@dataclass
class Stats:
    utterances: int = 0
    wakes: int = 0
    requests: int = 0
    ignored: int = 0  # speech that did not start with the wake phrase
    suppressed: int = 0  # frames dropped because Artemis was speaking
    capped: int = 0  # utterances cut at max_s because the room never went quiet
    misheard: int = 0  # wakes from the quick model that the full model did not confirm
    events: list[Event] = field(default_factory=list)


class WakeLoop:
    """Frames in, events out.

    - "Hey Artemis, do X" in one breath gives `request` with "do X".
    - "Hey Artemis" on its own gives `wake`; the next utterance within `window_s` is the
      request, with no need to say the phrase again.
    - Speed: a request needs the segmenter's full end silence (0.8 s), since people pause mid-sentence.
      The wake phrase does not. After `wake_silence_s` (0.4 s) of quiet, the utterance so far is
      transcribed once: a bare "Hey Artemis" arms at once (the armed tone and Listening come then, not
      0.4 s and a transcription later), and the rest is cut off so the next words are the request. Anything
      else waits for the end as before, and when nothing more was said by then that early transcript is
      the final one, so a one-breath "Hey Artemis, do X" is not transcribed twice either.
    - With a `quick` transcriber (Whisper tiny.en: about 0.2 s where small.en takes 0.9 s on David's
      laptop), the early look asks it first. A bare "Hey Artemis" from it arms at once, and the full model
      checks the same audio on the next frame, while the user starts talking: if it disagrees, `misheard`
      takes the wake back. So tiny.en only ever makes the wake sooner, never a request on its own. Speech
      it hears no wake phrase in waits for the end, so other people's talk is still transcribed once. It is
      asked only when the voice before the pause is short enough to be a bare "Hey Artemis" (`quick_max_s`),
      so a one-breath request does not wait for both models.
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
        vad: Callable[[np.ndarray], float | None] | None = None,  # VoiceDetector: endpoint on voice, not loudness
        clean: bool = True,  # denoise each utterance with the room's own noise before transcribing it
        wake_silence_s: float | None = 0.4,  # quiet that is enough to hear a bare "Hey Artemis"; None: wait for the end
        quick: Callable[[np.ndarray], str | None] | None = None,  # fast, rougher, for the early look; None: not ready
        quick_max_s: float = 1.5,  # more voice than this before the pause is not a bare "Hey Artemis" (about 0.9 s)
    ) -> None:
        self.transcribe = transcribe
        self.quick = quick
        self.quick_frames = round(quick_max_s * RATE / FRAME)
        self._confirm: np.ndarray | None = None  # audio of a quick wake, for the full model to check
        self.vad = vad
        self.clean = clean
        self._room: deque[np.ndarray] = deque(maxlen=15)  # the last 1.5 s of nobody talking
        self.speaking = speaking
        self.segmenter = segmenter or Segmenter()
        self.window_frames = round(window_s * RATE / FRAME)
        self.tail_frames = round(tail_s * RATE / FRAME)
        self.stats = Stats()
        self._armed = 0  # frames left in which a request needs no wake phrase
        self._tail = 0
        self.wake_frames = round(wake_silence_s * RATE / FRAME) if wake_silence_s else 0
        self._early: tuple[int, int, str, float] | None = None  # (utterance, voiced frames, transcript, seconds)
        self._looked = 0  # the utterance last given an early look: one per utterance, at its first pause

    @property
    def armed(self) -> bool:
        return self._armed > 0

    def feed(self, frame: np.ndarray) -> Event | None:
        if self._confirm is not None:
            audio, self._confirm = self._confirm, None
            try:
                confirmed = wake_request(self._hear(audio)[0]) is not None
            except Exception:  # unconfirmed is not confirmed: tiny.en alone must never lead to a request
                confirmed = False
            if not confirmed:  # the full model heard no "Hey Artemis": take it back
                self._armed = 0
                self.stats.wakes -= 1
                self.stats.misheard += 1
                return self._emit(Event("misheard"))
        talking = self.speaking()
        if talking:
            self._tail = self.tail_frames
        if talking or self._tail > 0:
            if not talking:
                self._tail -= 1
            self.segmenter.reset()
            self._early = None
            self.stats.suppressed += 1
            return None

        was_armed = self.armed
        if self._armed and not self.segmenter.in_speech:
            self._armed -= 1
            if self._armed == 0:
                return self._emit(Event("timeout"))

        voice = self.vad(frame) if self.vad is not None else None
        if not self.segmenter.in_speech and (voice is None or voice < 0.2):
            self._room.append(frame)  # what the room sounds like with nobody talking: the noise to remove
        seg = self.segmenter
        audio = seg.feed(frame, voice)
        if audio is None:
            if (self.wake_frames and not was_armed and seg.in_speech and seg.quiet_frames == self.wake_frames
                    and seg.voiced >= seg.min_frames and seg.started != self._looked):
                return self._early_look()
            return None
        self.stats.utterances += 1
        if seg.capped:
            self.stats.capped += 1
        seconds = len(audio) / RATE
        early, self._early = self._early, None
        if early is not None and early[:2] == (seg.started, seg.last_voiced):
            # Nothing was said after the early look: its transcript is the final one. It ran during the
            # rest of the end silence, so only what outlasted that is still waited for.
            quiet = seg.end_frames * FRAME / RATE
            text, took = early[2], max(0.0, early[3] - (seg.end_frames - self.wake_frames) * FRAME / RATE)
        else:
            text, took = self._hear(audio)
            quiet = 0.0 if seg.capped else seg.end_frames * FRAME / RATE
        del audio  # nothing is kept once it has been transcribed
        if not text:
            return None
        phantom = text.lower().strip(" .,!?") in HALLUCINATIONS  # noise Whisper read as a stock phrase

        request = wake_request(text)
        if request is not None:
            self.stats.wakes += 1
        elif was_armed:
            if phantom:
                return None  # after "Hey Artemis", a stock phrase from noise must not become a request
            request = text  # inside the window after "Hey Artemis": no phrase needed
        else:
            self.stats.ignored += 1
            return self._emit(Event("ignored", f"{len(text.split())} words", seconds))
        if request == "":
            self._armed = self.window_frames
            return self._emit(Event("wake", seconds=seconds, quiet_s=quiet, whisper_s=took))
        self.stats.requests += 1
        self._armed = 0
        return self._emit(Event("request", request, seconds, quiet, took))

    def _hear(self, audio: np.ndarray, transcribe: Callable[[np.ndarray], str | None] | None = None) -> tuple:
        """(transcript, seconds it took), the audio denoised first. The transcript is None when a quick
        transcriber is not ready."""
        began = time.monotonic()
        if self.clean and len(self._room) >= 5:
            audio = denoise(audio, np.concatenate(self._room))
        text = (transcribe or self.transcribe)(audio)
        return (None if text is None else text.strip()), time.monotonic() - began

    def _early_look(self) -> Event | None:
        """A pause after the first words: if they were just "Hey Artemis", arm now (see the class doc)."""
        seg = self.segmenter
        self._looked = seg.started
        audio = seg.so_far()
        took = 0.0
        if self.quick is not None and seg.voiced <= self.quick_frames:
            text, took = self._hear(audio, self.quick)
            found = None if text is None else wake_request(text)
            if found == "":
                self._confirm = audio
                return self._arm_early(audio, took)
            if text is not None and found is None:
                return None  # most likely other people talking: the full model hears it once, at the end
        text, more = self._hear(audio)  # the full model: its transcript can be the final one
        took += more
        if wake_request(text) != "":
            self._early = (seg.started, seg.voiced, text, took)  # kept for the end, if nothing more is said
            return None
        return self._arm_early(audio, took)

    def _arm_early(self, audio: np.ndarray, took: float) -> Event:
        self.segmenter.reset()  # what follows the pause is the request, a new utterance
        self.stats.utterances += 1
        self.stats.wakes += 1
        self._armed = self.window_frames
        return self._emit(Event("wake", seconds=len(audio) / RATE, quiet_s=self.wake_frames * FRAME / RATE,
                                whisper_s=took))

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


QUICK_MODEL = "tiny.en"


def quick_model() -> str | None:
    """ARTEMIS_WAKE_QUICK_MODEL: the fast model that spots a bare "Hey Artemis" (default tiny.en); "off" for none."""
    raw = os.environ.get("ARTEMIS_WAKE_QUICK_MODEL", "").strip()
    if raw.lower() in ("off", "none", "0"):
        return None
    return raw or QUICK_MODEL


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
        self._quick_name: str | None = None
        self._quick_model = None  # loaded on its own thread, so neither startup nor the mic is held up
        make = loop_factory or WakeLoop
        if loop_factory is None and stream_factory is None:
            # the voice detector and the quick model are for the real microphone (tests feed synthetic tones)
            vad = VoiceDetector.load()
            self._quick_name = quick_model()
            self.wake = make(self._transcribe, speaking=self._muted, vad=vad,
                             segmenter=Segmenter(pre_roll_s=0.5) if vad is not None else None,
                             quick=self._quick if self._quick_name else None)
        else:
            self.wake = make(self._transcribe, speaking=self._muted)

    @property
    def armed(self) -> bool:
        return self.wake.armed

    def _muted(self) -> bool:
        """Drop the microphone's audio: Artemis is talking, or push-to-talk owns the mic."""
        voice = self.voice
        if getattr(voice, "busy_listening", getattr(voice, "listening", False)) or getattr(voice, "transcribing", False):
            return True
        announcer = getattr(voice, "announcer", None)
        if announcer is None:
            return False
        try:
            if announcer.speaking_engine() is not None:
                return True
            return bool(announcer.current().busy())  # a queued or downloading line is about to be heard
        except Exception:
            return True  # can't tell: safer not to listen than to hear ourselves

    def _transcribe(self, audio):
        return self.voice._transcribe(audio)

    def _load_quick(self) -> None:
        if self._quick_name is None:
            return
        try:
            from faster_whisper import WhisperModel
            self._quick_model = WhisperModel(self._quick_name, device="cpu", compute_type="int8")
        except Exception as e:  # not downloaded and offline, say: the full model does the early look alone
            log.warning("Wake word: the quick model %s is not available (%s); small.en spots the phrase alone",
                        self._quick_name, type(e).__name__)

    def _quick(self, audio) -> str | None:
        """The quick model's transcript, or None while it is not loaded (the full model then decides)."""
        model = self._quick_model
        if model is None:
            return None
        segments, _ = model.transcribe(audio, language="en", beam_size=1, vad_filter=True,
                                       initial_prompt=getattr(self.voice, "prompt", None) or None)
        return " ".join(s.text.strip() for s in segments)

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stream = self._stream_factory(self._on_audio)
        self._stream.start()
        self._thread = threading.Thread(target=self._run, name="artemis-wake", daemon=True)
        self._thread.start()
        if self._quick_name is not None:  # its own thread: frames heard meanwhile are judged on time, not late
            threading.Thread(target=self._load_quick, name="artemis-wake-quick", daemon=True).start()
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
            try:
                event = self.wake.feed(frame)
            except Exception as e:  # one bad utterance must not end hands-free listening
                log.warning("Wake word: an utterance failed (%s)", type(e).__name__)
                continue
            if event is None:
                continue
            self._handle(event)

    def _handle(self, event: Event) -> None:
        if event.kind in ("wake", "request"):  # numbers only: how long after the last word it was decided (warning: core.log keeps warning+)
            log.warning("Wake word: %s %.2f s after the last word (%.1f s quiet + %.2f s Whisper)", event.kind,
                     event.quiet_s + event.whisper_s, event.quiet_s, event.whisper_s)
        if event.kind == "wake":
            self._earcon("listening")
        elif event.kind == "request":
            self._earcon("stopped")
            if self._loop is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self.on_request, event.text)
        elif event.kind == "ignored":
            log.info("Wake word: ignored speech (%s)", event.text)  # a word count, never the words
        elif event.kind in ("timeout", "misheard"):
            self._earcon("stopped")

    def _earcon(self, name: str) -> None:
        play = getattr(self.voice, "_earcon", None)
        if play is not None:
            try:
                play(name)
            except Exception:
                pass
