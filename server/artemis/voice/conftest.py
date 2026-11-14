"""Voice tests never use a real ElevenLabs key or engine choice from the developer's shell."""

import pytest


@pytest.fixture(autouse=True)
def _no_voice_env(monkeypatch):
    for name in ("ELEVENLABS_API_KEY", "ARTEMIS_VOICE_ENGINE", "ARTEMIS_ELEVEN_VOICE_ID"):
        monkeypatch.delenv(name, raising=False)
