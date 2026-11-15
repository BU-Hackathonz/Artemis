"""WhisperVoice: contracts.md 3.3 Voice with a real microphone.

Push-to-talk: the client calls start on key down and stop on key up. Start stops current speech,
plays the "listening" earcon to the end, and only then opens the mic, so neither Artemis's voice
nor the beep is transcribed (contracts.md 5.1). Stop plays the "stopped" earcon and closes the mic
at once; it plays it on every release, even one during the "listening" earcon. sounddevice records
16 kHz mono into memory; faster-whisper transcribes locally on the CPU. Audio is never written to disk
and is dropped as soon as it is transcribed (handoff: minimal audio retention).

Settings (environment):
- ARTEMIS_WHISPER_MODEL: default small.en; medium.en is more accurate, slower.
- ARTEMIS_WHISPER_BEAM: default 1 (greedy, fastest); 1 to 10, e.g. 5.
- ARTEMIS_PTT_POST_ROLL: default 0 (off); seconds from 0 to 1.0, e.g. 0.35. The mic stays open that long
  after release (people let go of the key while still saying the last word), closes, and only then plays
  "stopped", so that beep is never transcribed either.
- ARTEMIS_WHISPER_PROMPT: unset (default) is the word-list prompt; natural is the spoken-style lead (text.py).
- ARTEMIS_WHISPER_HOTWORDS: unset (default) is off; on also passes the prompt's fixed lead (wake word and demo
  words, text.py HOTWORDS) as faster-whisper's hotwords. Ignored by a faster-whisper without that parameter.
- ARTEMIS_WORKING_CUE: unset (default) keeps the core's spoken "Still working."; earcon plays a soft
  "working" tick instead (working_cue, contracts.md 3.3 and 5.7), only when a voice can speak, never into
  the mic, never while Whisper runs and never over Artemis's speech or another earcon. The core still puts
  "Still working." in the log, unspoken, so a screen reader can read it.
A beam above 1, a post-roll, the natural prompt and hotwords are opt-in: switch them on for the stage only after
docs/demo.md row 3 (the demo sentence, word for word) passes with them on, on the demo laptop.

Every transcript then gets text.py's fixes for known mishearings (CORRECTIONS), push-to-talk and wake word alike.

Manual check on a laptop: python -m artemis.voice.listen  (records 4 s, prints the transcript and time)
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import threading
import time

import numpy as np

from . import earcons
from .speech import Announcer, Priority, RateStatus
from .text import HOTWORDS, Heard, correct_transcript, strip_wake_word, vocabulary_prompt

log = logging.getLogger(__name__)

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
MODEL = "small.en"  # ARTEMIS_WHISPER_MODEL overrides, e.g. medium.en (more accurate, slower)
# Greedy, as the demo has always run. Opt-in: ARTEMIS_WHISPER_BEAM=5 (faster-whisper's own default). Greedy
# misheard two hardware runs ("Artem, this is creative", "Prince") and can end a window early, skipping its
# last words, but beam 5 is slower and not yet proven on the demo laptop.
BEAM = 1
# Off by default. Opt-in: ARTEMIS_PTT_POST_ROLL=0.35 keeps the mic open that many seconds after release.
POST_ROLL = 0.0
MAX_POST_ROLL = 1.0
TICK_GUARD_S = 0.2  # after the working tick, for the room's echo: the wake listener stays deaf this long


def whisper_model() -> str:
    return os.environ.get("ARTEMIS_WHISPER_MODEL", "").strip() or MODEL


def whisper_beam() -> int:
    raw = os.environ.get("ARTEMIS_WHISPER_BEAM", "").strip()
    if not raw:
        return BEAM
    if raw.isdigit() and 1 <= int(raw) <= 10:
        return int(raw)
    log.warning("ARTEMIS_WHISPER_BEAM=%r is not a whole number from 1 to 10; using %d", raw, BEAM)
    return BEAM


def ptt_post_roll() -> float:
    raw = os.environ.get("ARTEMIS_PTT_POST_ROLL", "").strip()
    if not raw:
        return POST_ROLL
    try:
        seconds = float(raw)
    except ValueError:
        seconds = -1.0
    if 0.0 <= seconds <= MAX_POST_ROLL:  # also rejects nan and inf
        return seconds
    log.warning("ARTEMIS_PTT_POST_ROLL=%r is not a number of seconds from 0 to %s; using %s", raw, MAX_POST_ROLL, POST_ROLL)
    return POST_ROLL


def whisper_natural_prompt() -> bool:
    """ARTEMIS_WHISPER_PROMPT=natural picks the spoken-style prompt lead; unset or anything else, the word list."""
    return os.environ.get("ARTEMIS_WHISPER_PROMPT", "").strip().lower() == "natural"


def whisper_hotwords() -> bool:
    """ARTEMIS_WHISPER_HOTWORDS=on also passes the fixed demo vocabulary as hotwords; unset or anything else, off."""
    return os.environ.get("ARTEMIS_WHISPER_HOTWORDS", "").strip().lower() == "on"


def takes_hotwords(model) -> bool:
    """faster-whisper 1.0+ has transcribe(hotwords=...); an older one (or another model) does not."""
    try:
        params = inspect.signature(model.transcribe).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "hotwords" or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)


def working_earcon_on() -> bool:
    """ARTEMIS_WORKING_CUE=earcon swaps the spoken "Still working." for a soft tick; anything else keeps the line."""
    return os.environ.get("ARTEMIS_WORKING_CUE", "").strip().lower() == "earcon"


def loudest_rms(audio: np.ndarray) -> float:
    """RMS of the loudest 100 ms window."""
    n = len(audio) // WINDOW
    if n == 0:
        return float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
    frames = audio[: n * WINDOW].reshape(n, WINDOW)
    return float(np.sqrt(np.mean(np.square(frames), axis=1)).max())


class VoiceError(Exception):
    """Microphone problems the core should turn into an `error` event."""


def _sentence(prefix: str, error: Exception) -> str:
    """'prefix: detail.' always ending in a full stop, so the spoken recovery tip after it is a new sentence."""
    detail = str(error).strip().rstrip(".")
    text = f"{prefix}: {detail}" if detail else prefix
    return text if text.endswith(("!", "?")) else text + "."


def _input_stream(callback):
    import sounddevice as sd

    return sd.InputStream(samplerate=RATE, channels=1, dtype="float32", callback=callback)


class WhisperVoice:
    def __init__(self, announcer: Announcer | None = None, model_name: str | None = None, model=None, stream_factory=None,
                 earcon=earcons.play, beam_size: int | None = None, post_roll: float | None = None,
                 natural_prompt: bool | None = None, working_earcon: bool | None = None,
                 hotwords: bool | None = None) -> None:
        self.announcer = announcer or Announcer()
        self.model_name = model_name or whisper_model()
        self.beam_size = beam_size or whisper_beam()
        self.post_roll = ptt_post_roll() if post_roll is None else post_roll
        self.natural_prompt = whisper_natural_prompt() if natural_prompt is None else natural_prompt
        self.working_earcon = working_earcon_on() if working_earcon is None else working_earcon
        self.hotwords = whisper_hotwords() if hotwords is None else hotwords
        self._model = model
        self._stream_factory = stream_factory or _input_stream
        self._play_earcon = earcon
        self._earcon_until = 0.0  # monotonic time this voice's last earcon ends; no tick before it
        self._tick_until = 0.0  # the last tick's end: the wake listener drops the mic's audio until then
        self.wake_listener = None  # set by the core when ARTEMIS_LISTEN_MODE=wake (it has .armed)
        self._stream = None
        self.transcribing = False  # Whisper is turning the last recording into text (voice.activity)
        self._wanted = False  # key is held; cleared by stop, even while the start earcon plays
        self._gen = 0  # bumped by every press and release, so a stale start never opens the mic
        self._tail: tuple[asyncio.Event, asyncio.Event] | None = None  # (cut, closed) during an opt-in post-roll
        self._chunks: list[np.ndarray] = []
        self._frames = 0
        self._lock = threading.Lock()  # the audio callback runs on PortAudio's thread
        self.prompt = vocabulary_prompt([], natural=self.natural_prompt)  # wake word and demo words, even before a project opens

    @property
    def listening(self) -> bool:
        return self._stream is not None

    @property
    def working_cue(self):
        """contracts.md 3.3: the core plays this instead of saying "Still working." while a task runs.

        None (the default, or when no voice can speak) makes the core keep its spoken line, so a user
        whose speech goes through the client's live region still gets the words, not a silent tick.
        """
        if not self._working_earcon_on() or not self.announcer.voice_ready:
            return None
        return self._tick

    def _working_earcon_on(self) -> bool:
        """The Announcer's working_cue_earcon, set by apply() from the voice profile (which already holds
        ARTEMIS_WORKING_CUE), wins when it is on. Otherwise the env value read at construction (or passed in).
        A settings PATCH cannot turn the cue off while ARTEMIS_WORKING_CUE pins it on (409), so the two agree."""
        if getattr(self.announcer, "working_cue_earcon", False):
            return True
        return self.working_earcon

    def _earcon(self, name: str) -> float:
        """Play one of this voice's earcons and note when it ends, so a tick never cuts it off."""
        duration = self._play_earcon(name) or 0.0
        self._earcon_until = max(self._earcon_until, time.monotonic() + duration)
        return duration

    def _tick(self) -> bool:
        """Play the "working" tick if nothing else owns the sound or the mic. True if it played.

        Skipped beats are not made up: the next heartbeat (about 3 s later) tries again.
        """
        if self.busy_listening or self.transcribing:
            return False  # push-to-talk is under way or Whisper is running: never a sound in that cycle
        wake = self.wake_listener
        if wake is not None and wake.armed:
            return False  # heard "Hey Artemis": the mic is recording the request
        if time.monotonic() < self._earcon_until:
            return False  # "listening" or "stopped" is still playing; a tick would cut it off
        try:
            if self.announcer.current().busy() or self.announcer.speaking_engine() is not None:
                return False  # never over Artemis's speech, including a line ElevenLabs handed to the fallback
        except Exception:
            return False  # can't tell: silence is safer than a tick over speech
        duration = self._earcon("working")
        self._tick_until = time.monotonic() + duration + TICK_GUARD_S
        return True

    @property
    def tick_playing(self) -> bool:
        """The tick is audible now (plus a short tail): the wake listener drops the mic's audio meanwhile."""
        return time.monotonic() < self._tick_until

    @property
    def busy_listening(self) -> bool:
        """True for the whole push-to-talk cycle, from the press until the mic is closed.

        Wider than `listening` (the mic stream is open): it also covers the listening earcon before
        the mic opens, and the opt-in post-roll after release, when the mic is still recording. The
        core must not play a sound in that window, or it would land in the request.
        """
        return self._wanted or self._stream is not None or self._tail is not None

    def warm(self) -> None:
        """Load the model now (about 3 s, longer on first download) instead of on the first request."""
        # Importing sounddevice initialises PortAudio, which probes every audio device: 1.5 s on David's
        # Ubuntu laptop. Done lazily, that landed on the first push-to-talk, so the mic opened about 1.4 s
        # after the tone. Doing it here, at startup, makes the first press as fast as the rest.
        try:
            import sounddevice  # noqa: F401
        except Exception as e:  # no audio stack: push-to-talk reports it when used
            log.warning("Audio could not be initialised at startup: %s", type(e).__name__)
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
            self._prime()

    def _prime(self) -> None:
        """One throwaway pass at startup. The first real decode paid the one-time setup (the VAD model, CTranslate2's
        first allocations, cold disk reads right after login): 11-15 s on David's laptop. The result is discarded."""
        started = time.monotonic()
        try:
            noise = np.random.default_rng(0).normal(0, 1e-3, RATE).astype(np.float32)  # 1 s, near silence
            list(self._model.transcribe(noise, language="en", beam_size=self.beam_size, vad_filter=True)[0])  # loads the VAD
            list(self._model.transcribe(noise, language="en", beam_size=self.beam_size, vad_filter=False)[0])  # runs the decoder
            log.info("Speech model primed in %.1f s", time.monotonic() - started)
        except Exception as e:  # priming is only for speed: the first request still works without it
            log.warning("Speech model priming failed (%s); the first request may be slow", type(e).__name__)

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
        if self._tail is not None:  # pressed again during the last release's (opt-in) post-roll: end it now
            cut, closed = self._tail
            cut.set()
            await closed.wait()  # that mic is closed before this beep plays, so it can't record it
            if gen != self._gen:
                return  # released again meanwhile; that stop already played "stopped"
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
            raise VoiceError(_sentence("Microphone not available", e)) from e
        self._stream = stream

    async def stop_listening(self, client_request_id: str) -> str:
        """The transcript. An empty release says why in `dropped` (short, silent or empty)."""
        held = self._wanted  # False for a repeated stop, or after start failed and spoke its error
        self._wanted = False
        self._gen += 1
        if self._stream is None:
            if held:
                self._earcon("stopped")  # released during the listening earcon: still say the mic is off
                return Heard("", "short")
            return ""  # not a release: a repeated stop has no reason, so the core reacts once per release
        stream, self._stream = self._stream, None
        if self.post_roll > 0:
            audio, rolled = await self._roll_then_close(stream)
        else:  # the default: no post-roll
            self._earcon("stopped")  # mic is about to close; no need to wait
            audio, rolled = self._close(stream), 0.0
        # Each drop is logged with its reason and numbers only, never words, so an empty push-to-talk
        # can be told apart: a tap, a silent hold, or (later) Whisper's no-speech filter.
        if len(audio) < RATE * (MIN_SECONDS + rolled):
            log.warning("Release dropped: too short (%.2f s of audio, needs %.2f s)", len(audio) / RATE, MIN_SECONDS + rolled)
            return Heard("", "short")
        level = loudest_rms(audio)
        if level < MIN_RMS:
            log.warning("Release dropped: silent (loudest 100 ms RMS %.5f, needs %.3f)", level, MIN_RMS)
            return Heard("", "silent")  # nobody spoke: the core says "I didn't hear anything."
        self.transcribing = True
        try:
            text = await asyncio.to_thread(self._transcribe, audio)
        finally:
            self.transcribing = False
        text = strip_wake_word(text)
        return text if text else Heard("", "empty")  # sound, but no words (or only the wake word)

    def _close(self, stream) -> np.ndarray:
        """Close the mic and take its audio."""
        try:
            stream.stop()
            stream.close()
        except Exception as e:  # mic unplugged mid-recording
            with self._lock:
                self._chunks, self._frames = [], 0
            raise VoiceError(_sentence("Microphone stopped unexpectedly", e)) from e
        with self._lock:
            audio = np.concatenate(self._chunks)[: RATE * MAX_SECONDS] if self._chunks else np.zeros(0, np.float32)
            self._chunks, self._frames = [], 0
        return audio

    async def _roll_then_close(self, stream) -> tuple[np.ndarray, float]:
        """Opt-in post-roll: keep the mic open for self.post_roll seconds (a new press cuts it short), close it,
        take its audio, and only then play "stopped", so the beep is never recorded. Returns (audio, seconds rolled)."""
        cut, closed = asyncio.Event(), asyncio.Event()
        self._tail = (cut, closed)
        began = time.monotonic()
        try:
            await asyncio.wait_for(cut.wait(), self.post_roll)  # the end of the last word is still arriving
        except TimeoutError:
            pass
        finally:
            rolled = time.monotonic() - began
            try:
                audio = self._close(stream)
            finally:
                self._tail = None
                closed.set()
                if not cut.is_set():  # a press during the post-roll plays its own "listening" instead
                    self._earcon("stopped")
        return audio, rolled

    def _transcribe(self, audio: np.ndarray) -> str:
        self.warm()
        # Pass numpy, not a file: faster-whisper's file decoder breaks on current PyAV.
        extra = {"hotwords": HOTWORDS} if self.hotwords and takes_hotwords(self._model) else {}  # opt-in
        segments, _ = self._model.transcribe(
            audio, language="en", beam_size=self.beam_size, vad_filter=True, initial_prompt=self.prompt or None, **extra
        )
        kept = []
        for s in segments:
            if getattr(s, "no_speech_prob", 0.0) <= MAX_NO_SPEECH:
                kept.append(s)
            else:  # where and how sure, never the words: tells a dropped last phrase from a dropped "Thank you."
                log.warning("Dropped a segment Whisper scored as probably not speech (no_speech_prob %.2f, %.1f-%.1f s of %.1f s)",
                            s.no_speech_prob, getattr(s, "start", 0.0), getattr(s, "end", 0.0), len(audio) / RATE)
        return correct_transcript(" ".join(s.text.strip() for s in kept).strip())

    def set_vocabulary(self, words: list[str]) -> None:
        self.prompt = vocabulary_prompt(words, natural=self.natural_prompt)

    async def announce(self, text: str, priority: Priority) -> None:
        if priority == "low" and (self.busy_listening or self.transcribing):
            # A low line ("Still working.", progress) said now would be recorded into the request, or
            # talk over a release; low lines may be dropped (contracts.md §2). The log still has it.
            log.debug("Low line not spoken: push-to-talk has the microphone")
            return
        await self.announcer.announce(text, priority)

    async def stop_speech(self) -> None:
        await self.announcer.stop_speech()

    async def repeat_last(self) -> None:
        await self.announcer.repeat_last()

    async def set_rate(self, delta: int) -> RateStatus:
        return await self.announcer.set_rate(delta)


if __name__ == "__main__":
    async def _demo() -> None:
        v = WhisperVoice()
        v.warm()
        await v.announce("Speak now for four seconds.", "high")
        await asyncio.sleep(2)  # let the prompt finish so the mic doesn't hear it
        await v.start_listening()
        await asyncio.sleep(4)
        began = time.monotonic()
        text = await v.stop_listening("cr_demo")
        print(repr(text), f"{time.monotonic() - began:.1f} s after release ({v.model_name}, beam {v.beam_size}, "
              f"post-roll {v.post_roll} s, {'natural' if v.natural_prompt else 'list'} prompt, "
              f"hotwords {'on' if v.hotwords else 'off'})")
        await v.announce(f"Heard: {text}" if text else "I didn't hear anything.", "high")

    asyncio.run(_demo())
