"""Voice profile: Artemis's own voice settings, from a JSON file and overridden by environment variables.

The file is ~/.artemis/voice.json unless ARTEMIS_VOICE_PROFILE names another path. It holds only the
fields of VoiceProfile, never a credential: the ElevenLabs key stays in ELEVENLABS_API_KEY. A bad value
is ignored with one warning that names the field (never the value), and the default is kept. load()
never raises.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from artemis.voice.speech import ENGINES, PLACEHOLDER_VOICE  # one source for the engine names and the unset id

log = logging.getLogger(__name__)

SPEED_MIN, SPEED_MAX = 0.7, 1.2
# Same range as speech.py's SAPI rate: past 6 words blur together, below -3 drags.
RATE_MIN, RATE_MAX = -3, 6
VOICE_ID_MAX = 64
SAPI_VOICE_MAX = 128

PATH_ENV = "ARTEMIS_VOICE_PROFILE"
ENV_VARS = {
    "engine": "ARTEMIS_VOICE_ENGINE",
    "eleven_voice_id": "ARTEMIS_ELEVEN_VOICE_ID",
    "eleven_speed": "ARTEMIS_ELEVEN_SPEED",
    "sapi_voice": "ARTEMIS_SAPI_VOICE",
    "sapi_rate": "ARTEMIS_SAPI_RATE",
    "working_cue": "ARTEMIS_WORKING_CUE",
}

_VOICE_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
_SECRET_NAME = re.compile(r"key|token|secret", re.IGNORECASE)  # no field with these names is ever written


@dataclass
class VoiceProfile:
    engine: str = "elevenlabs"
    eleven_voice_id: str | None = None
    eleven_speed: float = 1.0
    sapi_voice: str | None = None
    sapi_rate: int | None = None  # None keeps the Windows rate
    working_cue: bool = False  # True: a soft tick instead of the spoken "Still working."


class _Invalid(ValueError):
    """A value that a field does not accept."""


_SKIP = object()  # an environment variable that sets nothing: unset, empty or the placeholder


def _check_engine(value: Any) -> str:
    if not isinstance(value, str) or value not in ENGINES:
        raise _Invalid
    return value


def _check_voice_id(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Invalid
    text = value.strip()
    if text == PLACEHOLDER_VOICE:
        return None
    if not 0 < len(text) <= VOICE_ID_MAX or not _VOICE_ID_RE.fullmatch(text):
        raise _Invalid
    return text


def _check_speed(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _Invalid
    speed = float(value)
    if not math.isfinite(speed) or not SPEED_MIN <= speed <= SPEED_MAX:
        raise _Invalid
    return speed


def _check_sapi_voice(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Invalid
    text = value.strip()
    if not text or len(text) > SAPI_VOICE_MAX:
        raise _Invalid
    return text


def _check_sapi_rate(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _Invalid
    if not RATE_MIN <= value <= RATE_MAX:
        raise _Invalid
    return value


def _check_working_cue(value: Any) -> bool:
    if not isinstance(value, bool):
        raise _Invalid
    return value


_CHECKS = {
    "engine": _check_engine,
    "eleven_voice_id": _check_voice_id,
    "eleven_speed": _check_speed,
    "sapi_voice": _check_sapi_voice,
    "sapi_rate": _check_sapi_rate,
    "working_cue": _check_working_cue,
}


def default_path(environ: Mapping[str, str] | None = None) -> Path:
    """~/.artemis/voice.json, or the path in ARTEMIS_VOICE_PROFILE."""
    env = os.environ if environ is None else environ
    override = (env.get(PATH_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".artemis" / "voice.json"


def _from_env(field: str, raw: str) -> Any:
    """The typed value of one environment variable. _SKIP when it sets nothing; _Invalid when it is bad."""
    text = raw.strip()
    if not text:
        return _SKIP
    if field == "engine":
        return _check_engine(text.lower())
    if field == "eleven_voice_id":
        return _SKIP if text == PLACEHOLDER_VOICE else _check_voice_id(text)
    if field == "eleven_speed":
        try:
            number = float(text)
        except ValueError:
            raise _Invalid from None
        return _check_speed(number)
    if field == "sapi_voice":
        return _check_sapi_voice(text)
    if field == "sapi_rate":
        try:
            number = int(text)
        except ValueError:
            raise _Invalid from None
        return _check_sapi_rate(number)
    if field == "working_cue":
        word = text.lower()  # the same words listen.py already reads
        if word == "earcon":
            return True
        if word in ("off", "spoken"):
            return False
        raise _Invalid
    raise _Invalid


def _read_file(profile: VoiceProfile, path: Path) -> None:
    """Apply the JSON file's valid fields to `profile`. A missing file is normal and logs nothing."""
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:  # bad JSON, or bytes that are not UTF-8
        log.warning("voice profile %s is not valid JSON; using the defaults", path)
        return
    except Exception:  # unreadable (permissions, a directory, ...): never raise from load()
        log.warning("voice profile %s could not be read; using the defaults", path)
        return
    if not isinstance(data, dict):
        log.warning("voice profile %s is not a JSON object; using the defaults", path)
        return
    unknown = [key for key in data if key not in _CHECKS]
    if unknown:
        # The count only: an unknown key's name or value could be anything, so neither is logged.
        log.warning("voice profile %s has %d unknown key(s); ignoring them", path, len(unknown))
    for name, check in _CHECKS.items():
        if name not in data:
            continue
        try:
            setattr(profile, name, check(data[name]))
        except _Invalid:
            log.warning("voice profile %s: %s is not valid; keeping the default", path, name)


def _apply_env(profile: VoiceProfile, environ: Mapping[str, str]) -> None:
    """Environment variables override the file. A bad one is ignored with a warning naming the variable."""
    for field, var in ENV_VARS.items():
        raw = environ.get(var)
        if raw is None:
            continue
        try:
            value = _from_env(field, raw)
        except _Invalid:
            log.warning("%s is not valid; ignoring it", var)
            continue
        if value is not _SKIP:
            setattr(profile, field, value)


def load(path: Path | None = None, environ: Mapping[str, str] | None = None) -> VoiceProfile:
    """The voice profile: defaults, then the JSON file, then the environment. Never raises."""
    env = os.environ if environ is None else environ
    profile = VoiceProfile()
    target = Path(path) if path is not None else default_path(env)
    _read_file(profile, target)
    _apply_env(profile, env)
    return profile


def as_dict(profile: VoiceProfile) -> dict[str, Any]:
    """The public fields of `profile`, in dataclass order."""
    return {f.name: getattr(profile, f.name) for f in fields(profile) if not _SECRET_NAME.search(f.name)}


def save(profile: VoiceProfile, path: Path | None = None) -> None:
    """Write the profile as JSON, atomically: a temp file in the same directory, then os.replace."""
    target = Path(path) if path is not None else default_path()
    data = as_dict(profile)
    leaked = [name for name in data if _SECRET_NAME.search(name)]
    if leaked:  # as_dict filters these already; this guards against a future field
        raise ValueError("a voice profile never stores credentials")
    for name, check in _CHECKS.items():
        try:
            check(data[name])
        except _Invalid:
            raise ValueError(f"voice profile field {name} is not valid") from None

    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    text = json.dumps(data, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
