"""Voice settings routes (docs/contracts.md section 7): GET/PATCH /settings/voice, GET /voices, voice.status
engine, the ElevenLabs fallback notice, and the working-cue wiring. Fakes only: no network, no COM, no mic."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from artemis.agent.stub import StubAgent
from artemis.contracts import VoiceSettings, VoiceSettingsPatch
from artemis.core.app import Core, create_app
from artemis.policy.testing import FakeRunner
from artemis.voice import profile as voice_profile
from artemis.voice.profile import ENV_VARS, VoiceProfile
from artemis.voice.speech import FALLBACK_NOTICE, Announcer, ElevenLabsSpeaker

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ORIGIN = {"Origin": "http://tauri.localhost"}
FAKE_KEY = "fake_value_for_test_only"
PROFILE_KEYS = {"engine", "eleven_voice_id", "eleven_speed", "sapi_voice", "sapi_rate", "working_cue"}
DEFAULTS = {
    "voice_engine": "elevenlabs",
    "eleven_voice_id": None,
    "eleven_speed": 1.0,
    "sapi_voice": None,
    "sapi_rate": None,
    "working_cue": False,
    "active_engine": "elevenlabs",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Nothing from the developer's shell; the profile file goes to tmp_path."""
    for var in [*ENV_VARS.values(), "ELEVENLABS_API_KEY", "ARTEMIS_ELEVEN_MODEL"]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ARTEMIS_VOICE_PROFILE", str(tmp_path / "voice.json"))
    return tmp_path / "voice.json"


@pytest.fixture
def profile_path(clean_env) -> Path:
    return clean_env


class FakeAnnouncer:
    """The parts of Announcer the routes use. apply() records the profile and switches the engine."""

    def __init__(self, engine="elevenlabs"):
        self.engine = engine
        self.applied: list[VoiceProfile] = []
        self.state = (True, None)

    def status(self):
        return self.state

    def active_engine(self):
        return self.engine

    def apply(self, profile):
        self.applied.append(profile)
        self.engine = profile.engine

    async def watch(self, callback, interval_s=2.0):
        await asyncio.Event().wait()


class FakeVoice:
    def __init__(self, announcer):
        self.announcer = announcer
        self.spoken: list[tuple[str, str]] = []

    async def start_listening(self):
        pass

    async def stop_listening(self, client_request_id):
        return ""

    def set_vocabulary(self, words):
        pass

    async def announce(self, text, priority):
        self.spoken.append((priority, text))

    async def stop_speech(self):
        pass

    async def repeat_last(self):
        pass


def make_core(voice, tmp_path) -> Core:
    return Core(voice=voice, make_runner=FakeRunner, make_agent=StubAgent,
                projects_dir=tmp_path / "projects", log_dir=tmp_path / "logs")


@asynccontextmanager
async def serve(core):
    """The app with its lifespan running (as uvicorn does), through the ASGI transport."""
    app = create_app(core, TOKEN, warm_voice=False)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            yield client


async def wait_until(predicate, timeout=5.0):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


def voice_status_events(core):
    return [e for s in core.sessions.values() for e in s.bus.history if e.type == "voice.status"]


@pytest.fixture
def announcer():
    return FakeAnnouncer()


@pytest.fixture
def core(tmp_path, announcer):
    return make_core(FakeVoice(announcer), tmp_path)


# ----- auth and CORS -----


async def test_settings_and_voices_need_the_token(core):
    async with serve(core) as client:
        assert (await client.get("/settings/voice")).status_code == 401
        assert (await client.patch("/settings/voice", json={"sapi_rate": 1})).status_code == 401
        assert (await client.get("/voices")).status_code == 401
        wrong = {"Authorization": "Bearer wrong"}
        assert (await client.get("/settings/voice", headers=wrong)).status_code == 401
        assert (await client.get("/settings/voice", headers={**AUTH, "Origin": "null"})).status_code == 403


async def test_patch_is_allowed_for_the_listed_origin_only(core):
    async with serve(core) as client:
        ok = await client.options("/settings/voice", headers={
            **ORIGIN, "Access-Control-Request-Method": "PATCH", "Access-Control-Request-Headers": "authorization,content-type",
        })
        assert ok.status_code == 200
        assert "PATCH" in ok.headers["access-control-allow-methods"]
        assert ok.headers["access-control-allow-origin"] == "http://tauri.localhost"
        bad = await client.options("/settings/voice", headers={
            "Origin": "https://evil.example", "Access-Control-Request-Method": "PATCH",
        })
        assert "access-control-allow-origin" not in bad.headers


# ----- GET -----


async def test_get_defaults_with_no_file_and_no_env(core, profile_path):
    async with serve(core) as client:
        r = await client.get("/settings/voice", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == DEFAULTS
    assert not profile_path.exists()  # a GET never writes


async def test_get_reports_env_over_file_and_the_engine_speaking(core, profile_path, monkeypatch):
    profile_path.write_text(json.dumps({"sapi_rate": 2, "working_cue": True}))
    monkeypatch.setenv("ARTEMIS_SAPI_RATE", "4")
    async with serve(core) as client:
        r = await client.get("/settings/voice", headers=AUTH)
    assert r.json()["sapi_rate"] == 4  # the environment wins
    assert r.json()["working_cue"] is True  # the file still supplies what the environment does not set


# ----- PATCH: valid, saved, applied -----


async def test_patch_saves_applies_and_get_reflects_it(core, announcer, profile_path):
    body = {"voice_engine": "sapi", "sapi_rate": 2, "working_cue": True, "eleven_speed": 0.9,
            "sapi_voice": "Zira", "eleven_voice_id": "abc_123"}
    async with serve(core) as client:
        announcer.applied.clear()  # forget the startup apply
        r = await client.patch("/settings/voice", headers=AUTH, json=body)
        assert r.status_code == 200
        got = r.json()
        assert got["voice_engine"] == "sapi" and got["sapi_rate"] == 2 and got["working_cue"] is True
        assert got["eleven_speed"] == 0.9 and got["sapi_voice"] == "Zira" and got["eleven_voice_id"] == "abc_123"
        assert got["active_engine"] == "sapi"

        assert len(announcer.applied) == 1 and announcer.applied[0].engine == "sapi"
        again = await client.get("/settings/voice", headers=AUTH)
        assert again.json() == got

    stored = json.loads(profile_path.read_text())
    assert set(stored) == PROFILE_KEYS
    assert stored["engine"] == "sapi" and stored["sapi_rate"] == 2
    assert not any(name for name in stored if "key" in name.lower() or "token" in name.lower())


async def test_patch_changes_only_the_fields_sent(core, profile_path):
    async with serve(core) as client:
        await client.patch("/settings/voice", headers=AUTH, json={"sapi_rate": 2, "working_cue": True})
        r = await client.patch("/settings/voice", headers=AUTH, json={"sapi_rate": 4})
    assert r.json()["sapi_rate"] == 4 and r.json()["working_cue"] is True


async def test_patch_null_clears_a_nullable_field(core, profile_path):
    async with serve(core) as client:
        await client.patch("/settings/voice", headers=AUTH, json={"sapi_rate": 2})
        r = await client.patch("/settings/voice", headers=AUTH, json={"sapi_rate": None})
    assert r.status_code == 200 and r.json()["sapi_rate"] is None


# ----- PATCH: 422 invalid_setting -----


BAD_PATCHES = [
    ({"voice_engine": "azure"}, "voice_engine"),
    ({"voice_engine": None}, "voice_engine"),
    ({"voice_engine": "SAPI"}, "voice_engine"),
    ({"eleven_speed": 1.5}, "eleven_speed"),
    ({"eleven_speed": 0.5}, "eleven_speed"),
    ({"eleven_speed": "1"}, "eleven_speed"),
    ({"eleven_speed": True}, "eleven_speed"),
    ({"eleven_speed": None}, "eleven_speed"),
    ({"sapi_rate": 7}, "sapi_rate"),
    ({"sapi_rate": -4}, "sapi_rate"),
    ({"sapi_rate": "fast"}, "sapi_rate"),
    ({"sapi_rate": 2.0}, "sapi_rate"),
    ({"sapi_rate": True}, "sapi_rate"),
    ({"sapi_voice": ""}, "sapi_voice"),
    ({"sapi_voice": "x" * 129}, "sapi_voice"),
    ({"eleven_voice_id": "bad id!"}, "eleven_voice_id"),
    ({"eleven_voice_id": 5}, "eleven_voice_id"),
    ({"working_cue": "yes"}, "working_cue"),
    ({"working_cue": 1}, "working_cue"),
    ({"unknown_key": 1}, "unknown_key"),
    ({"active_engine": "sapi"}, "active_engine"),
]


@pytest.mark.parametrize("body,field", BAD_PATCHES)
async def test_bad_values_are_rejected_not_clamped(core, announcer, profile_path, body, field):
    async with serve(core) as client:
        announcer.applied.clear()
        r = await client.patch("/settings/voice", headers=AUTH, json=body)
    assert r.status_code == 422
    assert r.json() == {"error": "invalid_setting", "field": field}
    assert not profile_path.exists()  # nothing saved
    assert announcer.applied == []  # nothing applied


async def test_one_bad_field_saves_nothing_even_with_good_ones(core, profile_path):
    async with serve(core) as client:
        r = await client.patch("/settings/voice", headers=AUTH, json={"sapi_rate": 2, "voice_engine": "azure"})
    assert r.status_code == 422 and r.json()["field"] == "voice_engine"
    assert not profile_path.exists()


async def test_a_non_object_body_is_422_on_body(core, profile_path):
    async with serve(core) as client:
        for raw in (b"not json", b"[1, 2]"):
            r = await client.patch("/settings/voice", headers={**AUTH, "Content-Type": "application/json"}, content=raw)
            assert r.status_code == 422 and r.json() == {"error": "invalid_setting", "field": "body"}
    assert not profile_path.exists()


def test_the_model_refuses_active_engine_and_unknown_keys():
    assert "active_engine" not in VoiceSettingsPatch.model_fields
    assert set(VoiceSettings.model_fields) >= {"active_engine", "voice_engine", "sapi_rate"}


# ----- PATCH: 409 set_by_environment -----


@pytest.mark.parametrize("var,value,body,field", [
    ("ARTEMIS_SAPI_RATE", "3", {"sapi_rate": 1}, "sapi_rate"),
    ("ARTEMIS_VOICE_ENGINE", "sapi", {"voice_engine": "nvda"}, "voice_engine"),
    ("ARTEMIS_WORKING_CUE", "earcon", {"working_cue": False}, "working_cue"),
    ("ARTEMIS_ELEVEN_VOICE_ID", "abc123", {"eleven_voice_id": "other"}, "eleven_voice_id"),
])
async def test_a_field_pinned_by_the_environment_is_409_and_nothing_is_saved(core, profile_path, monkeypatch, var, value, body, field):
    monkeypatch.setenv(var, value)
    async with serve(core) as client:
        r = await client.patch("/settings/voice", headers=AUTH, json=body)
    assert r.status_code == 409
    assert r.json() == {"error": "set_by_environment", "field": field}
    assert not profile_path.exists()


async def test_other_fields_still_save_when_one_is_pinned(core, profile_path, monkeypatch):
    monkeypatch.setenv("ARTEMIS_SAPI_RATE", "3")
    async with serve(core) as client:
        r = await client.patch("/settings/voice", headers=AUTH, json={"sapi_rate": 1, "working_cue": True})
    assert r.status_code == 409 and r.json()["field"] == "sapi_rate"
    async with serve(core) as client:
        ok = await client.patch("/settings/voice", headers=AUTH, json={"working_cue": True})
    assert ok.status_code == 200 and ok.json()["working_cue"] is True


async def test_an_invalid_environment_value_pins_nothing(core, profile_path, monkeypatch):
    monkeypatch.setenv("ARTEMIS_SAPI_RATE", "fast")  # load() ignores it with a warning, so it pins nothing
    async with serve(core) as client:
        r = await client.patch("/settings/voice", headers=AUTH, json={"sapi_rate": 1})
    assert r.status_code == 200 and r.json()["sapi_rate"] == 1


# ----- GET /voices -----


async def test_voices_with_no_key_and_no_sapi_is_an_empty_list(core, monkeypatch):
    from artemis.voice import voices as voices_module

    # No SAPI, whatever this machine has installed: a Windows box with SAPI voices failed this test.
    asked = []
    monkeypatch.setattr(voices_module, "list_sapi_voices", lambda: asked.append(True) or [])
    async with serve(core) as client:
        r = await client.get("/voices", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"voices": []}
    assert asked == [True]  # the route really asked the (patched) SAPI list


async def test_voices_returns_only_engine_id_and_name_and_no_key(core, monkeypatch):
    from artemis.voice import voices as voices_module

    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    seen_keys = []

    def fake_sapi():
        return [{"engine": "sapi", "id": "Microsoft Zira", "name": "Microsoft Zira", "path": "C:/x"}]

    def fake_eleven(key, **_):
        seen_keys.append(key)
        return [{"engine": "elevenlabs", "id": "v1", "name": "Rachel", "api_key": key, "preview": "u"}]

    monkeypatch.setattr(voices_module, "list_sapi_voices", fake_sapi)
    monkeypatch.setattr(voices_module, "fetch_eleven_voices", fake_eleven)
    async with serve(core) as client:
        r = await client.get("/voices", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"voices": [
        {"engine": "elevenlabs", "id": "v1", "name": "Rachel"},
        {"engine": "sapi", "id": "Microsoft Zira", "name": "Microsoft Zira"},
    ]}
    assert seen_keys == [FAKE_KEY]  # the key went to the lookup only
    assert FAKE_KEY not in r.text


# ----- voice.status carries the engine -----


async def test_voice_status_includes_the_engine_at_session_start(core):
    async with serve(core) as client:
        sid = (await client.post("/sessions", headers=AUTH)).json()["session_id"]
        first = core.sessions[sid].bus.history[0]
    assert first.type == "voice.status"
    assert first.data == {"voice_ready": True, "screen_reader": None, "engine": "elevenlabs"}


async def test_patch_that_changes_the_engine_emits_voice_status_with_it(core, announcer):
    async with serve(core) as client:
        sid = (await client.post("/sessions", headers=AUTH)).json()["session_id"]
        before = len(voice_status_events(core))
        await client.patch("/settings/voice", headers=AUTH, json={"voice_engine": "sapi"})
        events = voice_status_events(core)
    assert len(events) == before + 1
    assert events[-1].data["engine"] == "sapi" and events[-1].data["voice_ready"] is True


async def test_patch_that_keeps_the_engine_emits_no_voice_status(core):
    async with serve(core) as client:
        await client.post("/sessions", headers=AUTH)
        before = len(voice_status_events(core))
        await client.patch("/settings/voice", headers=AUTH, json={"sapi_rate": 2})
    assert len(voice_status_events(core)) == before


# ----- startup and the saved file -----


async def test_the_saved_profile_is_applied_at_startup(tmp_path, profile_path):
    profile_path.write_text(json.dumps({"engine": "sapi", "working_cue": True}))
    announcer = FakeAnnouncer()
    async with serve(make_core(FakeVoice(announcer), tmp_path)) as client:
        r = await client.get("/settings/voice", headers=AUTH)
    assert announcer.applied and announcer.applied[0].engine == "sapi"
    assert announcer.applied[0].working_cue is True
    assert r.json()["active_engine"] == "sapi"


# ----- the ElevenLabs fallback notice -----


class QuietSpeaker:
    def __init__(self, name):
        self.name = name

    def available(self):
        return False

    def speak(self, text, interrupt):
        pass

    def cancel(self):
        pass

    def busy(self):
        return False


def real_announcer(eleven):
    return Announcer(nvda=QuietSpeaker("nvda"), sapi=QuietSpeaker("sapi"), is_nvda_running=lambda: False, eleven=eleven)


def failing_eleven(**kwargs):
    def boom(request, timeout_s):
        raise OSError("refused")

    return ElevenLabsSpeaker(api_key=FAKE_KEY, voice_id="voice123", timeout_s=0.5, fallback=None,
                             opener=boom, output=lambda: None, **kwargs)


def notices(core):
    return [e for s in core.sessions.values() for e in s.bus.history
            if e.type == "agent.message" and e.data.get("text") == FALLBACK_NOTICE]


async def test_giving_up_says_the_notice_once_at_normal_priority(tmp_path):
    eleven = failing_eleven()
    voice = FakeVoice(real_announcer(eleven))
    core = make_core(voice, tmp_path)
    assert eleven.on_give_up is not None  # wired at construction
    async with serve(core) as client:
        sid = (await client.post("/sessions", headers=AUTH)).json()["session_id"]
        for n in range(5):  # three failures in a row give up; two more must not repeat the notice
            eleven.speak(f"line {n}", False)
        await asyncio.to_thread(eleven.wait_idle, 5)
        await wait_until(lambda: len(notices(core)) >= 1)
        await asyncio.sleep(0.1)
        found = notices(core)
    assert len(found) == 1
    assert found[0].session_id == sid
    assert found[0].announce.priority == "normal"
    assert found[0].data["kind"] == "answer"
    assert ("normal", FALLBACK_NOTICE) in voice.spoken


async def test_the_fallback_wiring_survives_apply(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTEMIS_VOICE_ENGINE", "elevenlabs")
    eleven = failing_eleven()
    core = make_core(FakeVoice(real_announcer(eleven)), tmp_path)
    core.apply_voice_profile(VoiceProfile(engine="elevenlabs", eleven_speed=0.9))
    assert eleven.speed == 0.9 and eleven.on_give_up == core._say_fallback_notice


async def test_apply_that_builds_a_new_elevenlabs_speaker_wires_it_too(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTEMIS_VOICE_ENGINE", "nvda")  # so Announcer builds no ElevenLabs speaker yet
    announcer = real_announcer(None)
    assert announcer.eleven is None
    monkeypatch.delenv("ARTEMIS_VOICE_ENGINE")
    core = make_core(FakeVoice(announcer), tmp_path)
    core.apply_voice_profile(VoiceProfile(engine="elevenlabs"))
    assert announcer.eleven is not None
    assert announcer.eleven.on_give_up == core._say_fallback_notice


# ----- the key never leaves -----


async def test_the_key_never_appears_in_responses_the_file_or_the_logs(tmp_path, profile_path, monkeypatch, caplog):
    from artemis.voice import voices as voices_module

    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    caplog.set_level(logging.DEBUG)

    def failing_list(key, **_):
        raise OSError("network down")

    monkeypatch.setattr(voices_module, "fetch_eleven_voices", failing_list)
    core = make_core(FakeVoice(FakeAnnouncer()), tmp_path)
    async with serve(core) as client:
        bodies = [
            (await client.get("/settings/voice", headers=AUTH)).text,
            (await client.patch("/settings/voice", headers=AUTH, json={"voice_engine": "sapi"})).text,
            (await client.get("/voices", headers=AUTH)).text,
            (await client.patch("/settings/voice", headers=AUTH, json={"api_key": FAKE_KEY})).text,
        ]
    assert all(FAKE_KEY not in body for body in bodies)
    assert FAKE_KEY not in profile_path.read_text()
    assert FAKE_KEY not in caplog.text


# ----- working cue: the Announcer's setting, through the listen.py check -----


class SpeakingSapi:
    name = "sapi"

    def available(self):
        return True

    def speak(self, text, interrupt):
        pass

    def cancel(self):
        pass

    def busy(self):
        return False

    def choose_voice(self, name):
        return False

    def set_rate(self, rate):
        return rate


def working_voice(monkeypatch):
    pytest.importorskip("numpy")  # the voice group; listen.py imports numpy at load
    from artemis.voice.listen import WhisperVoice

    monkeypatch.setenv("ARTEMIS_VOICE_ENGINE", "sapi")
    announcer = Announcer(nvda=QuietSpeaker("nvda"), sapi=SpeakingSapi(), is_nvda_running=lambda: False)
    return WhisperVoice(announcer=announcer, model=object(), earcon=lambda name: None), announcer


def test_working_cue_follows_the_announcer_setting(monkeypatch):
    monkeypatch.delenv("ARTEMIS_WORKING_CUE", raising=False)
    voice, announcer = working_voice(monkeypatch)
    assert voice.working_cue is None  # off by default
    announcer.apply(VoiceProfile(engine="sapi", working_cue=True))
    assert callable(voice.working_cue)
    announcer.apply(VoiceProfile(engine="sapi", working_cue=False))
    assert voice.working_cue is None


def test_working_cue_env_still_works_without_an_apply(monkeypatch):
    monkeypatch.setenv("ARTEMIS_WORKING_CUE", "earcon")
    voice, _ = working_voice(monkeypatch)
    assert callable(voice.working_cue)


async def test_patch_working_cue_reaches_the_voice(tmp_path, monkeypatch):
    _, announcer = working_voice(monkeypatch)  # env: sapi; the voice's announcer is a real one
    core = make_core(FakeVoice(announcer), tmp_path)
    async with serve(core) as client:
        r = await client.patch("/settings/voice", headers=AUTH, json={"working_cue": True})
    assert r.status_code == 200
    assert announcer.working_cue_earcon is True


def test_the_settings_models_hold_no_key_field():
    for model in (VoiceSettings, VoiceSettingsPatch):
        assert not any("key" in name or "token" in name for name in model.model_fields)
