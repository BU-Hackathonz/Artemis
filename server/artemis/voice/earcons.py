"""Earcons: short, quiet tones generated in code (no audio files).

Rising pair = listening, falling pair = stopped. Three rising notes = success, two low falling notes =
failure, a high double knock = approval needed. All at VOLUME and at most 250 ms. The result and
approval sounds are defined here only: wiring them to events is a core change, not done yet.
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

RATE = 24_000
VOLUME = 0.15  # quieter than speech; never startling


def tone(freqs: list[int], ms_each: int = 70) -> np.ndarray:
    n = RATE * ms_each // 1000
    t = np.arange(n) / RATE
    fade = np.minimum(1, np.minimum(np.arange(n), n - 1 - np.arange(n)) / (RATE * 0.005))  # 5 ms ramps, no clicks
    return np.concatenate([np.sin(2 * np.pi * f * t) * fade for f in freqs]).astype(np.float32) * VOLUME


MAX_MS = 250  # an earcon is a cue, never a tune

SOUNDS = {
    "listening": tone([660, 880]),
    "stopped": tone([880, 660]),
    "success": tone([523, 659, 784], ms_each=60),  # C E G, rising: done
    "failure": tone([392, 311], ms_each=110),  # low and falling, below every other cue
    "approval": tone([988, 0, 988], ms_each=60),  # 0 Hz is a silent gap: knock, knock
}


def play(name: str) -> float:
    """Start playing an earcon and return its length in seconds; 0 if there is no output device."""
    try:
        import sounddevice as sd

        sd.play(SOUNDS[name], RATE)
    except Exception as e:  # an earcon must never block listening
        log.warning("Earcon %s failed: %s", name, e)
        return 0.0
    return len(SOUNDS[name]) / RATE
