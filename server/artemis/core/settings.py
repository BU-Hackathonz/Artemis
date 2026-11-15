"""Voice settings for the panel (docs/contracts.md section 7, proposed): the logic behind
GET and PATCH /settings/voice and GET /voices. The routes themselves live in app.py.

The file is the voice profile (artemis.voice.profile). Environment variables override the file, so a
field that the environment pins cannot be changed from here: the PATCH answers 409 set_by_environment
rather than saving a value that would not take effect. A PATCH writes the file alone, never the
environment's values. Only the voice fields listed in contracts/settings.py are readable or writable.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from artemis.contracts import VoiceSettings, VoiceSettingsPatch
from artemis.voice.profile import ENV_VARS, _SKIP, VoiceProfile, _from_env, _Invalid
from artemis.voice.voices import available_voices

# Model field -> profile field, in the order the model lists them.
PROFILE_FIELD = {
    "voice_engine": "engine",
    "eleven_voice_id": "eleven_voice_id",
    "eleven_speed": "eleven_speed",
    "sapi_voice": "sapi_voice",
    "sapi_rate": "sapi_rate",
    "working_cue": "working_cue",
}


def invalid_field(exc: ValidationError) -> str:
    """The first field the patch got wrong: its name, an unknown key's name, or "body" for a non-object."""
    for error in exc.errors():
        loc = error.get("loc") or ()
        if loc:
            return str(loc[0])
    return "body"


def pinned_fields(environ: Mapping[str, str]) -> set[str]:
    """Model fields that an environment variable sets to a valid value. An empty, placeholder or invalid
    variable sets nothing (load() ignores it), so it pins nothing."""
    pinned: set[str] = set()
    for model_field, profile_field in PROFILE_FIELD.items():
        raw = environ.get(ENV_VARS[profile_field])
        if raw is None:
            continue
        try:
            value = _from_env(profile_field, raw)
        except _Invalid:
            continue
        if value is not _SKIP:
            pinned.add(model_field)
    return pinned


def settings_from(profile: VoiceProfile, active_engine: str) -> VoiceSettings:
    return VoiceSettings(
        voice_engine=profile.engine,
        eleven_voice_id=profile.eleven_voice_id,
        eleven_speed=profile.eleven_speed,
        sapi_voice=profile.sapi_voice,
        sapi_rate=profile.sapi_rate,
        working_cue=profile.working_cue,
        active_engine=active_engine,
    )


def apply_patch(patch: VoiceSettingsPatch, profile: VoiceProfile) -> None:
    """Copy the fields the patch sent onto `profile`. Fields left out keep their value."""
    for model_field in patch.model_fields_set:
        setattr(profile, PROFILE_FIELD[model_field], getattr(patch, model_field))


def pinned_in_patch(patch: VoiceSettingsPatch, environ: Mapping[str, str]) -> str | None:
    """The first field the patch sends that the environment pins, in model order, or None."""
    pinned = pinned_fields(environ)
    for model_field in PROFILE_FIELD:
        if model_field in patch.model_fields_set and model_field in pinned:
            return model_field
    return None


async def list_voices() -> dict:
    """GET /voices: {"voices": [{engine, id, name}]} only. No key, path or other field leaves here."""
    entries = await available_voices()
    return {"voices": [{"engine": str(e["engine"]), "id": str(e["id"]), "name": str(e["name"])} for e in entries]}
