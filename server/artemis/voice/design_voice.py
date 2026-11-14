"""Design Artemis's own ElevenLabs voice from a description, then save the one you like to your account.

To use a ready-made voice instead of making one, run `pick`: it says the same Artemis lines in a
few ElevenLabs default voices, plays each, and saves the voice id of the one you choose.
    py .\\artemis\\voice\\design_voice.py pick

To make a new voice, run it with no arguments and answer the questions (it needs only Python, no project setup):
    py .\\artemis\\voice\\design_voice.py

It asks for the key (hidden as you paste), makes three previews and plays each one, asks which you
like, saves it as "Artemis", and offers to remember the voice id for Artemis. The key is never
printed, logged or written to a file.

The same steps one at a time: `design` writes preview-1.mp3 to preview-3.mp3 and prints each
preview's id; `create <id>` saves one and prints its voice id. Both read ELEVENLABS_API_KEY.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import subprocess
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

API = "https://api.elevenlabs.io/v1"
DESIGN_URL = f"{API}/text-to-voice/design?output_format=mp3_44100_128"
CREATE_URL = f"{API}/text-to-voice"
TIMEOUT_S = 60.0  # designing three previews takes a while

DEFAULT_NAME = "Artemis"
# Built for long listening sessions: a voice that works with you while you code, not a form being read.
DEFAULT_DESCRIPTION = (
    "A calm, warm, clear voice of an adult in their thirties with a neutral American accent. "
    "Mid-pitched, steady and unhurried, with crisp diction so file names, numbers and symbols are easy "
    "to follow. Friendly and confident like a patient pair programmer, never sing-song or dramatic. "
    "Studio-quality recording with no background noise."
)
# 100 to 1000 characters, as the API requires. Real Artemis lines, so the preview sounds like the job.
PREVIEW_TEXT = (
    "Artemis is ready. Hold Control Shift Space and speak, or type a request. "
    "Create project hello-world with one file, main.py. Enter to approve, Escape to reject. "
    "main.py ran successfully. It printed: Hello, World! "
    "main.py failed with exit code 1. NameError: name 'pritn' is not defined. Did you mean: 'print'?"
)


# Ready-made ElevenLabs default voices worth hearing for an assistant (docs: voices/default-voices).
CANDIDATES = (
    ("Sarah", "EXAVITQu4vr4xnSDxMaL", "female, American, soft"),
    ("Eric", "cjVigY5qzO86Huf0OWal", "male, American, friendly, conversational"),
    ("Jessica", "cgSgspJ2msm6clMCkdW9", "female, American, expressive, conversational"),
    ("Chris", "iP95p4xoKVk53GoZ742B", "male, American, casual, conversational"),
    ("Matilda", "XrExE9yKIg1WjnnlVkGX", "female, American, friendly"),
    ("Liam", "TX3LPaxmHKxFdv7VOQHJ", "male, American, articulate"),
)
SAMPLE_LINE = (
    "Artemis is ready. main.py ran successfully. It printed: Hello, World! "
    "Create project hello-world with one file. Enter to approve, Escape to reject."
)
TTS_URL = f"{API}/text-to-speech/{{voice_id}}?output_format=mp3_44100_128"
SAMPLE_MODEL = "eleven_flash_v2_5"  # the model Artemis speaks with


class ApiError(Exception):
    """A failed request. The message is the HTTP status or error type only: never the request."""


def _key(ask: bool = False) -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key and ask:
        key = getpass.getpass("Paste your ElevenLabs API key (hidden), then press Enter: ").strip()
    if not key:
        raise ApiError("ELEVENLABS_API_KEY is not set in this terminal")
    return key


def _play(path: Path) -> None:
    """Open an mp3 in the default player. Windows only; elsewhere the path is printed."""
    starter = getattr(os, "startfile", None)
    if starter is None:
        print(f"  Play it: {path}")
        return
    try:
        starter(str(path))
    except OSError:
        print(f"  Couldn't open it; play it yourself: {path}")


def _remember(voice_id: str) -> bool:
    """Save the voice id (not a secret) as a user environment variable, so new terminals pick it up."""
    if sys.platform != "win32":
        return False
    result = subprocess.run(["setx", "ARTEMIS_ELEVEN_VOICE_ID", voice_id], capture_output=True, text=True)
    return result.returncode == 0


def wizard(input_fn: Callable[[str], str] = input, play: Callable[[Path], None] = _play,
           remember: Callable[[str], bool] = _remember, opener: Callable | None = None,
           out_dir: Path = Path("voice-previews")) -> int:
    """Design, listen, pick, save: one question at a time."""
    key = _key(ask=True)
    description = DEFAULT_DESCRIPTION
    print("Making three previews of Artemis's voice. This takes about half a minute.")
    while True:
        ids = design(description, out_dir, key, opener)
        for n in range(1, len(ids) + 1):
            print(f"Playing preview {n} of {len(ids)}.")
            play(out_dir / f"preview-{n}.mp3")
            input_fn("Press Enter for the next one. ")
        choices = "/".join(str(n) for n in range(1, len(ids) + 1))
        while True:  # asking again costs nothing; only r makes new previews
            answer = input_fn(f"Which one should be Artemis's voice? Type {choices}, or r for three new ones: ").strip().lower()
            if answer == "r" or (answer.isdigit() and 1 <= int(answer) <= len(ids)):
                break
            print(f"Please type {choices} or r. The files are in {out_dir.resolve()} if you want to replay them.")
        if answer != "r":
            break
        change = input_fn("Describe what to change, or press Enter to keep the same description: ").strip()
        if change:
            description = f"{DEFAULT_DESCRIPTION} {change}"
    voice_id = create(ids[int(answer) - 1], DEFAULT_NAME, description, key, opener)
    print(f"Saved to your ElevenLabs account as {DEFAULT_NAME!r}. Voice id: {voice_id}")
    if input_fn("Remember this voice for Artemis on this computer? y or n: ").strip().lower().startswith("y"):
        if remember(voice_id):
            print("Done. New terminals use this voice. Open a new PowerShell window before starting Artemis.")
        else:
            print(f"Couldn't save it. Set it yourself: $env:ARTEMIS_ELEVEN_VOICE_ID = \"{voice_id}\"")
    else:
        print(f"To use it: $env:ARTEMIS_ELEVEN_VOICE_ID = \"{voice_id}\"")
    return 0


def _post(url: str, body: dict, key: str, opener: Callable | None = None) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "application/json"},
    )
    opener = opener or urllib.request.urlopen
    try:
        with opener(request, timeout=TIMEOUT_S) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ApiError(f"HTTP {e.code}{_detail(e)}") from None
    except Exception as e:  # URLError, timeout, bad JSON: the type only
        raise ApiError(type(e).__name__) from None


def _post_audio(url: str, body: dict, key: str, opener: Callable | None = None) -> bytes:
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
    )
    try:
        with opener(request, timeout=TIMEOUT_S) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        raise ApiError(f"HTTP {e.code}{_detail(e)}") from None
    except Exception as e:
        raise ApiError(type(e).__name__) from None


def samples(key: str, out_dir: Path, opener: Callable | None = None) -> list[tuple[str, str, Path | None, str]]:
    """Say SAMPLE_LINE in each candidate voice. Returns (name, voice id, file or None, note) per voice:
    a voice the plan can't use gets None and the reason, and the rest still run."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for name, voice_id, about in CANDIDATES:
        try:
            audio = _post_audio(TTS_URL.format(voice_id=voice_id), {"text": SAMPLE_LINE, "model_id": SAMPLE_MODEL}, key, opener)
        except ApiError as e:
            results.append((name, voice_id, None, str(e)))
            continue
        path = out_dir / f"{name.lower()}.mp3"
        path.write_bytes(audio)
        results.append((name, voice_id, path, about))
    return results


def pick(input_fn: Callable[[str], str] = input, play: Callable[[Path], None] | None = None,
         remember: Callable[[str], bool] | None = None, opener: Callable | None = None,
         out_dir: Path = Path("voice-samples")) -> int:
    """Hear the same Artemis lines in each ready-made voice, then choose one."""
    play, remember = play or _play, remember or _remember
    key = _key(ask=True)
    print(f"Making a sample in {len(CANDIDATES)} ready-made voices. This takes a few seconds each.")
    results = samples(key, out_dir, opener)
    usable = [r for r in results if r[2] is not None]
    for name, _, path, note in results:
        if path is None:
            print(f"{name}: not available ({note}).")
    if not usable:
        print("No sample could be made, so nothing was chosen.")
        return 1
    for n, (name, _, path, about) in enumerate(usable, start=1):
        print(f"{n}. {name} ({about}).")
        play(path)
        input_fn("Press Enter for the next one. ")
    choices = "/".join(str(n) for n in range(1, len(usable) + 1))
    while True:
        answer = input_fn(f"Which voice should Artemis use? Type {choices}: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(usable):
            break
        print(f"Please type {choices}. The samples are in {out_dir.resolve()}.")
    name, voice_id, _, _ = usable[int(answer) - 1]
    print(f"{name}. Voice id: {voice_id}")
    if input_fn("Remember this voice for Artemis on this computer? y or n: ").strip().lower().startswith("y"):
        if remember(voice_id):
            print("Done. New terminals use this voice. Open a new PowerShell window before starting Artemis.")
        else:
            print(f"Couldn't save it. Set it yourself: $env:ARTEMIS_ELEVEN_VOICE_ID = \"{voice_id}\"")
    else:
        print(f"To use it: $env:ARTEMIS_ELEVEN_VOICE_ID = \"{voice_id}\"")
    return 0


def _detail(e: urllib.error.HTTPError) -> str:
    """ElevenLabs' own error message (e.g. a plan limit), which names no secret. Empty if unreadable."""
    try:
        detail = json.loads(e.read().decode("utf-8")).get("detail")
    except Exception:
        return ""
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("status")
    return f": {detail}"[:200] if isinstance(detail, str) and detail else ""


def design(description: str, out_dir: Path, key: str, opener: Callable | None = None) -> list[str]:
    """Ask for previews of `description`; write them as mp3 files and return their ids."""
    reply = _post(DESIGN_URL, {"voice_description": description, "text": PREVIEW_TEXT}, key, opener)
    previews = reply.get("previews") if isinstance(reply, dict) else None
    if not isinstance(previews, list) or not previews:
        raise ApiError("no previews in the reply")
    out_dir.mkdir(parents=True, exist_ok=True)
    ids = []
    for n, preview in enumerate(previews, start=1):
        audio, voice_id = preview.get("audio_base_64"), preview.get("generated_voice_id")
        if not isinstance(audio, str) or not isinstance(voice_id, str):
            continue
        (out_dir / f"preview-{n}.mp3").write_bytes(base64.b64decode(audio))
        ids.append(voice_id)
    return ids


def create(generated_voice_id: str, name: str, description: str, key: str,
           opener: Callable | None = None) -> str:
    """Save a preview as a voice in the account and return its voice id."""
    reply = _post(CREATE_URL, {"voice_name": name, "voice_description": description,
                               "generated_voice_id": generated_voice_id}, key, opener)
    voice_id = reply.get("voice_id") if isinstance(reply, dict) else None
    if not isinstance(voice_id, str) or not voice_id:
        raise ApiError("no voice id in the reply")
    return voice_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m artemis.voice.design_voice", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    d = sub.add_parser("design", help="make three previews")
    d.add_argument("--description", default=DEFAULT_DESCRIPTION)
    d.add_argument("--out", type=Path, default=Path("voice-previews"))
    sub.add_parser("pick", help="hear ready-made voices and choose one (no new voice is made)")
    c = sub.add_parser("create", help="save one preview as a voice")
    c.add_argument("preview_id")
    c.add_argument("--name", default=DEFAULT_NAME)
    c.add_argument("--description", default=DEFAULT_DESCRIPTION)
    args = parser.parse_args(argv)
    try:
        if args.command is None:
            return wizard()
        if args.command == "pick":
            return pick()
        key = _key()
        if args.command == "design":
            ids = design(args.description, args.out, key)
            for n, voice_id in enumerate(ids, start=1):
                print(f"preview-{n}.mp3  id {voice_id}")
            print(f"Previews are in {args.out.resolve()}. Pick one, then run: create <id>")
        else:
            voice_id = create(args.preview_id, args.name, args.description, key)
            print(f"Saved as {args.name!r}. Voice id: {voice_id}")
            print("Use it with: $env:ARTEMIS_ELEVEN_VOICE_ID = \"" + voice_id + "\"")
    except ApiError as e:
        print(f"ElevenLabs request failed: {e}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Nothing was saved.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
