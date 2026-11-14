"""Design Artemis's own ElevenLabs voice from a description, then save the one you like to your account.

Run in your own terminal with the key set there (it is never printed, logged or written to a file):
    $env:ELEVENLABS_API_KEY = Read-Host "ElevenLabs key"
    uv run python -m artemis.voice.design_voice design            # three previews into voice-previews\\
    uv run python -m artemis.voice.design_voice create <preview>  # saves that preview as "Artemis"

`design` writes preview-1.mp3 to preview-3.mp3 and prints each preview's id. Listen, pick one, and run
`create` with its id. `create` prints the new voice id: put it in ARTEMIS_ELEVEN_VOICE_ID (or the voice
profile) and Artemis speaks with it. `--description` and `--name` change the defaults below.
"""

from __future__ import annotations

import argparse
import base64
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


class ApiError(Exception):
    """A failed request. The message is the HTTP status or error type only: never the request."""


def _key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise ApiError("ELEVENLABS_API_KEY is not set in this terminal")
    return key


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
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("design", help="make three previews")
    d.add_argument("--description", default=DEFAULT_DESCRIPTION)
    d.add_argument("--out", type=Path, default=Path("voice-previews"))
    c = sub.add_parser("create", help="save one preview as a voice")
    c.add_argument("preview_id")
    c.add_argument("--name", default=DEFAULT_NAME)
    c.add_argument("--description", default=DEFAULT_DESCRIPTION)
    args = parser.parse_args(argv)
    try:
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
