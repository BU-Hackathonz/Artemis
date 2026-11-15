"""Voice settings for the panel (docs/contracts.md section 7, proposed): GET and PATCH /settings/voice.

The checks are the voice profile's own (artemis.voice.profile._CHECKS), so the ranges live in one place.
They are imported lazily: the voice package imports this contracts package, so a module-level import
would be circular.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator

# The model calls the engine voice_engine; the profile calls it engine. Every other name is the same.
_PROFILE_NAME = {"voice_engine": "engine"}


def _profile_check(field_name: str, value: Any) -> Any:
    from artemis.voice.profile import _CHECKS  # lazy: see the module docstring

    return _CHECKS[_PROFILE_NAME.get(field_name, field_name)](value)


class VoiceSettings(BaseModel):
    """What GET /settings/voice returns: the effective voice settings, with the engine speaking now.

    Never a key field: the ElevenLabs key lives only in ELEVENLABS_API_KEY.
    """

    model_config = ConfigDict(extra="forbid")

    voice_engine: Literal["elevenlabs", "nvda", "sapi"]
    eleven_voice_id: str | None
    eleven_speed: float
    sapi_voice: str | None
    sapi_rate: int | None  # None keeps the Windows rate
    working_cue: bool  # True: a soft tick instead of the spoken "Still working."
    active_engine: str  # read-only: the speaker that would say the next line (elevenlabs, nvda, sapi or none)


class VoiceSettingsPatch(BaseModel):
    """What PATCH /settings/voice accepts. Only the fields sent are changed; a sent null is a value, and
    only the fields that accept null (eleven_voice_id, sapi_voice, sapi_rate) take one.

    Unknown keys and active_engine are rejected (extra forbidden). Each value goes through the profile check.
    """

    model_config = ConfigDict(extra="forbid")

    voice_engine: Literal["elevenlabs", "nvda", "sapi"] | None = None
    eleven_voice_id: str | None = None
    eleven_speed: float | None = None
    sapi_voice: str | None = None
    sapi_rate: int | None = None
    working_cue: bool | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _check_value(cls, value: Any, info: ValidationInfo) -> Any:
        return _profile_check(info.field_name, value)
