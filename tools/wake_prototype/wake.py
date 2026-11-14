"""Wake-word prototype, the pure part: no microphone, no Whisper, no Artemis imports.

Three pieces, each testable offline:

- `Segmenter` cuts a stream of audio frames into utterances by loudness.
- `wake_request` decides from a transcript whether it began with "Hey Artemis".
- `WakeLoop` joins them: frames in, events out. It never starts a task and cannot approve
  anything; the most it produces is the text of a request for a person (or, later, the
  core) to look at.

Nothing here stores audio. An utterance lives in memory until it has been transcribed and is
then dropped.
"""

from __future__ import annotations

import re
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
        min_rms: float = 0.004,  # never treat anything quieter than this as speech
        over_floor: float = 3.0,  # speech must be this many times the noise floor
        end_silence_s: float = 0.8,
        min_s: float = 0.4,  # shorter is a click or a cough
        max_s: float = 12.0,
        pre_roll_s: float = 0.3,  # kept from before the start, so the "H" of "Hey" isn't lost
    ) -> None:
        self.min_rms = min_rms
        self.over_floor = over_floor
        self.end_frames = max(1, round(end_silence_s * RATE / FRAME))
        self.min_frames = max(1, round(min_s * RATE / FRAME))
        self.max_frames = max(1, round(max_s * RATE / FRAME))
        self.floor = 0.0
        self._pre: deque[np.ndarray] = deque(maxlen=max(1, round(pre_roll_s * RATE / FRAME)))
        self._chunks: list[np.ndarray] = []
        self._voiced = 0
        self._quiet = 0
        self.capped = False  # the last utterance ended because of max_s, not silence

    @property
    def threshold(self) -> float:
        return max(self.min_rms, self.floor * self.over_floor)

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
