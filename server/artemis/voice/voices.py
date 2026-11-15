"""Voices the user can pick from: installed SAPI voices and, when a key is set, ElevenLabs voices.

Only names and ids leave this module: {"engine", "id", "name"} per voice. The ElevenLabs key is
sent in the xi-api-key header and is never put in a URL, argv or a log line.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import urllib.request
from typing import Callable
from urllib.error import HTTPError

log = logging.getLogger(__name__)

ELEVEN_VOICES_URL = "https://api.elevenlabs.io/v1/voices"
MAX_ELEVEN_VOICES = 200


def list_sapi_voices() -> list[dict]:
    """Installed SAPI voices, enumerated the way SapiSpeaker.choose_voice matches them.

    choose_voice accepts ARTEMIS_SAPI_VOICE as a substring of GetDescription(), so the description is
    both the id and the name: the id can be pasted straight into that variable. pywin32 is imported
    here, not at module import, so Linux CI never needs it. Any failure gives [].
    """
    if sys.platform != "win32":
        return []
    try:
        import pythoncom
        import win32com.client
    except Exception:  # pywin32 missing: no SAPI on this machine
        return []
    try:
        pythoncom.CoInitialize()  # COM is per thread; this runs on a worker thread
        try:
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            names = [token.GetDescription() for token in voice.GetVoices()]
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:
        log.warning("could not list SAPI voices: %s", type(e).__name__)
        return []
    return [{"engine": "sapi", "id": name, "name": name} for name in names if name]


def fetch_eleven_voices(key: str, *, opener: Callable = urllib.request.urlopen, timeout_s: float = 3.0) -> list[dict]:
    """ElevenLabs voices for `key`. The key goes in the header only. Any failure logs a type or status and gives []."""
    if not key:
        return []
    request = urllib.request.Request(ELEVEN_VOICES_URL, headers={"xi-api-key": key, "Accept": "application/json"}, method="GET")
    try:
        with opener(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as e:
        log.warning("ElevenLabs voice list failed: HTTP %s", e.code)
        return []
    except Exception as e:  # URLError, timeouts, bad JSON (ValueError) and anything else: type name only
        log.warning("ElevenLabs voice list failed: %s", type(e).__name__)
        return []

    raw = payload.get("voices") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        log.warning("ElevenLabs voice list had no voices list")
        return []
    voices = []
    for item in raw[:MAX_ELEVEN_VOICES]:
        if not isinstance(item, dict):
            continue
        voice_id, name = item.get("voice_id"), item.get("name")
        if isinstance(voice_id, str) and voice_id and isinstance(name, str) and name:
            voices.append({"engine": "elevenlabs", "id": voice_id, "name": name})
    return voices


def _only_fields(entries: list[dict]) -> list[dict]:
    return [{"engine": str(e["engine"]), "id": str(e["id"]), "name": str(e["name"])} for e in entries]


async def available_voices(
    *,
    sapi_list: Callable[[], list[dict]] | None = None,
    eleven_list: Callable[[str], list[dict]] | None = None,
    environ: dict | None = None,
) -> list[dict]:
    """ElevenLabs voices first (when a key is set and the list loads), then SAPI voices.

    Both lookups run off the event loop and concurrently. No ELEVENLABS_API_KEY means no network call.
    """
    env = os.environ if environ is None else environ
    sapi_list = list_sapi_voices if sapi_list is None else sapi_list
    eleven_list = fetch_eleven_voices if eleven_list is None else eleven_list
    key = (env.get("ELEVENLABS_API_KEY") or "").strip()

    async def sapi_part() -> list[dict]:
        try:
            return await asyncio.to_thread(sapi_list)
        except Exception as e:
            log.warning("could not list SAPI voices: %s", type(e).__name__)
            return []

    async def eleven_part() -> list[dict]:
        if not key:
            return []
        try:
            return await asyncio.to_thread(eleven_list, key)
        except Exception as e:
            log.warning("ElevenLabs voice list failed: %s", type(e).__name__)
            return []

    eleven_entries, sapi_entries = await asyncio.gather(eleven_part(), sapi_part())
    return _only_fields(eleven_entries) + _only_fields(sapi_entries)
