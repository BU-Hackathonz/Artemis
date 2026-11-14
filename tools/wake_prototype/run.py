"""Wake-word prototype, the part that needs a real microphone.

Run it from server/ on Windows with the voice group installed:

    uv run --group voice python ../tools/wake_prototype/run.py --minutes 2

It listens, and prints what it would have done. It is not connected to Artemis: it starts no
task, sends nothing anywhere, and cannot approve anything. Audio stays in memory and is dropped
after each utterance is transcribed locally; speech that doesn't start with "Hey Artemis" is
shown only as a word count unless you pass --show-ignored.

See README.md for the test to run and the numbers that decide go or no-go.
"""

from __future__ import annotations

import argparse
import queue
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from wake import FRAME, RATE, Segmenter, WakeLoop  # noqa: E402

# The same model and filters as push-to-talk (server/artemis/voice/listen.py).
MODEL = "small.en"
MAX_NO_SPEECH = 0.6
PROMPT = "Hey Artemis,"  # nudges Whisper toward the right spelling of the name


def make_transcriber(model_name: str):
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    timings: list[tuple[float, float]] = []  # (audio seconds, transcription seconds)

    def transcribe(audio: np.ndarray) -> str:
        started = time.monotonic()
        segments, _ = model.transcribe(audio, language="en", beam_size=1, vad_filter=True, initial_prompt=PROMPT)
        kept = [s for s in segments if getattr(s, "no_speech_prob", 0.0) <= MAX_NO_SPEECH]
        text = " ".join(s.text.strip() for s in kept).strip()
        timings.append((len(audio) / RATE, time.monotonic() - started))
        return text

    return transcribe, timings


def make_speaking_check(mode: str):
    """True while Artemis's own voice is talking. Only SAPI can tell us; NVDA can't."""
    if mode == "never":
        return (lambda: False), None
    try:
        from artemis.voice.speech import Announcer
    except ImportError:
        print("artemis.voice is not importable; running without the self-wake guard.")
        return (lambda: False), None
    announcer = Announcer()

    def speaking() -> bool:
        try:
            return bool(announcer.current().busy())
        except Exception:
            return False

    return speaking, announcer


def earcon() -> None:
    try:
        from artemis.voice import earcons

        earcons.play("listening")
    except Exception:
        pass  # a missing beep must not stop the test


def main() -> int:
    parser = argparse.ArgumentParser(description="Hey Artemis wake-word prototype (prints only).")
    parser.add_argument("--minutes", type=float, default=2.0, help="how long to listen")
    parser.add_argument("--device", default=None, help="input device name or index (default: system default)")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--guard", choices=["sapi", "never"], default="sapi", help="ignore audio while Artemis speaks")
    parser.add_argument("--say", default=None, help="have Artemis speak this line every 20 s, to test self-wakes")
    parser.add_argument("--show-ignored", action="store_true", help="print the text of speech that didn't wake it")
    parser.add_argument("--min-rms", type=float, default=0.004)
    args = parser.parse_args()

    import sounddevice as sd

    print(f"Loading {args.model}...")
    transcribe, timings = make_transcriber(args.model)
    shown: list[str] = []

    def transcribe_and_keep(audio: np.ndarray) -> str:
        text = transcribe(audio)
        shown.append(text)
        return text

    speaking, announcer = make_speaking_check(args.guard)
    loop = WakeLoop(transcribe_and_keep, speaking=speaking, segmenter=Segmenter(min_rms=args.min_rms))
    frames: queue.Queue[np.ndarray] = queue.Queue()

    def on_audio(data, count, when, status) -> None:
        frames.put(data[:, 0].copy())

    device = int(args.device) if args.device and args.device.isdigit() else args.device
    deadline = time.monotonic() + args.minutes * 60
    next_say = time.monotonic() + 5
    print(f"Listening for {args.minutes:g} minutes. THE MICROPHONE IS OPEN. Ctrl+C stops it.")
    try:
        with sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=FRAME, device=device, callback=on_audio):
            while time.monotonic() < deadline:
                if args.say and announcer and time.monotonic() >= next_say:
                    announcer.current().speak(args.say, interrupt=True)
                    next_say = time.monotonic() + 20
                try:
                    frame = frames.get(timeout=0.5)
                except queue.Empty:
                    continue
                event = loop.feed(frame)
                if event is None:
                    continue
                stamp = time.strftime("%H:%M:%S")
                if event.kind == "wake":
                    earcon()
                    print(f"{stamp}  WAKE      (listening for the request)")
                elif event.kind == "request":
                    earcon()
                    print(f"{stamp}  REQUEST   {event.text!r}  [{event.seconds:.1f} s of audio]")
                elif event.kind == "timeout":
                    print(f"{stamp}  timeout   (no request followed the wake)")
                else:
                    detail = repr(shown[-1]) if args.show_ignored and shown else event.text
                    print(f"{stamp}  ignored   {detail}")
    except KeyboardInterrupt:
        print("Stopped.")
    print("THE MICROPHONE IS CLOSED.")

    s = loop.stats
    print()
    print(f"utterances heard:        {s.utterances}")
    print(f"wakes:                   {s.wakes}")
    print(f"requests captured:       {s.requests}")
    print(f"ignored (no wake phrase): {s.ignored}")
    print(f"cut at the length cap:   {s.capped}   (the room never went quiet)")
    print(f"frames dropped while Artemis spoke: {s.suppressed}")
    if timings:
        audio = sum(a for a, _ in timings)
        spent = sum(t for _, t in timings)
        worst = max(t for _, t in timings)
        print(f"transcription: {spent:.1f} s for {audio:.1f} s of audio; slowest single utterance {worst:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
