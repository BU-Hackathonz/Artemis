"""Hardware check for the ElevenLabs voice: speaks a few lines and prints the time to first audio.

Run in your own terminal with the key set there (it is never printed):
    $env:ELEVENLABS_API_KEY = Read-Host "ElevenLabs key"   # Artemis's voice is the default; ARTEMIS_ELEVEN_VOICE_ID picks another
    uv run --group voice python -m artemis.voice.eleven_check
"""

from __future__ import annotations

import statistics
import sys

from .speech import CACHED_LINES, ElevenLabsSpeaker, NullSpeaker

LINES = [
    "main.py ran successfully. It printed: Hello, World!",
    "Create project hello-world with 1 file: main.py, 1 line. No security findings. Enter to approve, Escape to reject.",
    "main.py failed with exit code 1. NameError: name 'pritn' is not defined.",
    "Canceled.",
    "Canceled.",  # the second time comes from the in-memory cache
]


def main() -> int:
    speaker = ElevenLabsSpeaker(fallback=NullSpeaker())
    if not speaker.available():
        print("ElevenLabs is not configured: set ELEVENLABS_API_KEY in this terminal.")
        return 1
    times = []
    for line in LINES:
        speaker.speak(line, interrupt=False)
        speaker.wait_idle(30)
        first = speaker.last_first_audio_s
        source = "cache" if line in CACHED_LINES and first == 0.0 else "network"
        print(f"{'failed' if first is None else f'{first:.2f} s'} to first audio ({source}): {line[:50]}")
        if first and source == "network":
            times.append(first)
        speaker.last_first_audio_s = None
    if times:
        print(f"Median time to first audio over the network: {statistics.median(times):.2f} s ({len(times)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
