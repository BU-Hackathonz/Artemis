"""voice.activity: listening, transcribing, speaking and idle from the voice's real state (contracts.md section 2)."""

import asyncio

import pytest

from artemis.contracts.events import VoiceActivityData
from artemis.core.test_settings import TOKEN, make_core, serve, wait_until  # noqa: F401  (shared helpers)
from artemis.voice.speech import Announcer


class FakeSpeaker:
    def __init__(self, name, busy=False, available=True):
        self.name, self._busy, self._available = name, busy, available

    def available(self):
        return self._available

    def busy(self):
        return self._busy

    def speak(self, text, interrupt):
        pass

    def cancel(self):
        pass


class Eleven(FakeSpeaker):
    def __init__(self):
        super().__init__("elevenlabs", busy=True)  # a line is queued (downloading)...
        self.playing = False  # ...but no audio plays yet

    def speaking(self):
        return self.playing


class Voice:
    def __init__(self, announcer):
        self.announcer = announcer
        self.busy_listening = False
        self.transcribing = False

    async def start_listening(self):
        pass

    async def stop_listening(self, client_request_id):
        return ""

    def set_vocabulary(self, words):
        pass

    async def announce(self, text, priority):
        pass

    async def stop_speech(self):
        pass

    async def repeat_last(self):
        pass


def activity(core):
    return [(e.data["state"], e.data["engine"]) for s in core.sessions.values() for e in s.bus.history
            if e.type == "voice.activity"]


def test_speaking_engine_reports_audible_speech_only():
    eleven = Eleven()
    a = Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: False, eleven=eleven)
    assert a.speaking_engine() is None  # queued is not speaking
    eleven.playing = True
    assert a.speaking_engine() == "elevenlabs"
    eleven.playing = False
    a.sapi._busy = True
    assert a.speaking_engine() == "sapi"
    a.sapi._busy, a.nvda._busy = False, True
    assert a.speaking_engine() == "nvda"  # Artemis's own estimate for NVDA


def test_the_data_model_holds_state_and_engine_only():
    assert set(VoiceActivityData.model_fields) == {"state", "engine"}
    with pytest.raises(ValueError):
        VoiceActivityData(state="thinking")


async def test_transitions_are_sent_once_each_and_in_order(tmp_path):
    eleven = Eleven()
    announcer = Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: False, eleven=eleven)
    voice = Voice(announcer)
    core = make_core(voice, tmp_path)
    async with serve(core) as client:
        r = await client.post("/sessions", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code in (200, 201)
        # A real release goes straight from listening to transcribing (the mic closes, then Whisper runs).
        voice.busy_listening = True
        await wait_until(lambda: activity(core) and activity(core)[-1][0] == "listening")
        voice.transcribing, voice.busy_listening = True, False
        await wait_until(lambda: activity(core)[-1][0] == "transcribing")
        voice.transcribing = False
        await wait_until(lambda: activity(core)[-1] == ("idle", None))
        eleven.playing = True
        await wait_until(lambda: activity(core)[-1] == ("speaking", "elevenlabs"))
        eleven.playing = False
        await wait_until(lambda: activity(core)[-1] == ("idle", None))
        await asyncio.sleep(0.3)  # no repeats while nothing changes
    assert activity(core) == [("listening", None), ("transcribing", None), ("idle", None),
                              ("speaking", "elevenlabs"), ("idle", None)]


async def test_a_new_session_hears_the_current_state_only_when_busy(tmp_path):
    announcer = Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi", busy=True), is_nvda_running=lambda: False,
                          eleven=Eleven())
    core = make_core(Voice(announcer), tmp_path)
    async with serve(core) as client:
        await wait_until(lambda: core.activity == ("speaking", "sapi"))
        r = await client.post("/sessions", headers={"Authorization": f"Bearer {TOKEN}"})
        sid = r.json()["session_id"]
        events = [e for e in core.sessions[sid].bus.history if e.type == "voice.activity"]
        assert [(e.data["state"], e.data["engine"]) for e in events][:1] == [("speaking", "sapi")]


class ArmedWake:
    def __init__(self):
        self.armed = False

    def stop(self):
        pass


async def test_wake_armed_is_listening_and_a_wake_request_becomes_a_task(tmp_path):
    announcer = Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: False, eleven=Eleven())
    core = make_core(Voice(announcer), tmp_path)
    async with serve(core) as client:
        sid = (await client.post("/sessions", headers={"Authorization": f"Bearer {TOKEN}"})).json()["session_id"]
        core.wake = ArmedWake()
        core.wake.armed = True  # "Hey Artemis" was heard on its own
        await wait_until(lambda: core.activity == ("listening", None))
        core.wake.armed = False
        await wait_until(lambda: core.activity == ("idle", None))
        core.wake_request("create a Python program that prints Hello World")
        session = core.sessions[sid]
        await wait_until(lambda: any(e.type == "transcript.final" for e in session.bus.history))
        heard = [e for e in session.bus.history if e.type == "transcript.final"][0]
        assert heard.data["text"] == "create a Python program that prints Hello World"
        await wait_until(lambda: any(e.type == "task.state" for e in session.bus.history))


def test_start_wake_without_whisper_keeps_push_to_talk(tmp_path, caplog):
    core = make_core(Voice(Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: False)), tmp_path)
    core.start_wake()  # the fake voice has no _transcribe, like the stub voice
    assert core.wake is None and "needs the Whisper voice" in caplog.text
