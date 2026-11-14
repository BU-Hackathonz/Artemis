"""Earcons: short, quiet tones generated in code (no audio files). Rising = listening, falling = stopped."""

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


SOUNDS = {"listening": tone([660, 880]), "stopped": tone([880, 660])}


def play(name: str) -> float:
    """Start playing an earcon and return its length in seconds; 0 if there is no output device."""
    try:
        import sounddevice as sd

        sd.play(SOUNDS[name], RATE)
    except Exception as e:  # an earcon must never block listening
        log.warning("Earcon %s failed: %s", name, e)
        return 0.0
    return len(SOUNDS[name]) / RATE
