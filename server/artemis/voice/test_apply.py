"""Announcer.apply and active_engine: switching Artemis's voice while running. Fakes only: no network, no COM."""

import asyncio
import json
import time

from artemis.voice import speech
from artemis.voice.profile import VoiceProfile
from artemis.voice.speech import Announcer, ElevenLabsSpeaker

KEY = "sk_test_never_show_this_1234567890"


class FakeNvda:
    """Records every call made on it, including attribute reads that are not part of routing."""

    name = "nvda"

    def __init__(self, available=True):
        self.calls, self.said, self._available = [], [], available

    def available(self):
        self.calls.append("available")
        return self._available

    def speak(self, text, interrupt):
        self.calls.append("speak")
        self.said.append((text, interrupt))

    def cancel(self):
        self.calls.append("cancel")

    def busy(self):
        self.calls.append("busy")
        return False

    def __getattr__(self, attr):
        self.calls.append(attr)
        raise AttributeError(attr)


class FakeSapi:
    name = "sapi"

    def __init__(self, available=True, set_rate_error=None):
        self._available, self.set_rate_error = available, set_rate_error
        self.said, self.chosen, self.rates = [], [], []

    def available(self):
        return self._available

    def speak(self, text, interrupt):
        self.said.append((text, interrupt))

    def cancel(self):
        pass

    def busy(self):
        return False

    def choose_voice(self, name):
        self.chosen.append(name)
        return True

    def set_rate(self, rate):
        if self.set_rate_error is not None:
            raise self.set_rate_error
        self.rates.append(rate)
        return rate


class FakeResponse:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def read(self, n):
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        pass


class FakeOutput:
    def __init__(self, per_write_s=0.0):
        self.per_write_s, self.written, self.aborted = per_write_s, [], False

    def start(self):
        pass

    def write(self, chunk):
        if self.aborted:
            raise RuntimeError("stream aborted")
        time.sleep(self.per_write_s)
        self.written.append(chunk)

    def stop(self):
        pass

    def abort(self):
        self.aborted = True

    def close(self):
        pass


class Opener:
    def __init__(self, make_response=lambda: FakeResponse([b"\x01\x00" * 10])):
        self.make_response, self.requests = make_response, []

    def __call__(self, request, timeout_s):
        self.requests.append(request)
        return self.make_response()


def eleven_speaker(opener=None, outputs=None, fallback=None, per_write_s=0.0, api_key=KEY, voice_id="voice_abc"):
    outputs = [] if outputs is None else outputs

    def make_output():
        outputs.append(FakeOutput(per_write_s))
        return outputs[-1]

    return ElevenLabsSpeaker(api_key=api_key, voice_id=voice_id, speed=1.0, fallback=fallback,
                             opener=opener or Opener(), output=make_output)


def announcer(eleven=None, sapi=None, nvda=None, running=False):
    return Announcer(nvda=nvda if nvda is not None else FakeNvda(), sapi=sapi if sapi is not None else FakeSapi(),
                     is_nvda_running=lambda: running, eleven=eleven)


def say(a, text):
    asyncio.run(a.announce(text, "normal"))


def wait_for(condition, timeout_s=2.0):
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for playback"
        time.sleep(0.01)


def test_apply_switches_who_speaks_the_next_line():
    opener, sapi = Opener(), FakeSapi()
    e = eleven_speaker(opener, fallback=sapi)
    a = announcer(eleven=e, sapi=sapi)

    a.apply(VoiceProfile(engine="elevenlabs", eleven_voice_id="voice_abc"))
    say(a, "one")
    assert e.wait_idle(2) and a.active_engine() == "elevenlabs" and len(opener.requests) == 1

    a.apply(VoiceProfile(engine="sapi"))
    assert a.active_engine() == "sapi"
    say(a, "two")
    assert sapi.said == [("two", False)] and len(opener.requests) == 1

    a.apply(VoiceProfile(engine="elevenlabs", eleven_voice_id="voice_abc"))
    assert a.active_engine() == "elevenlabs"
    say(a, "three")
    assert e.wait_idle(2) and len(opener.requests) == 2
    assert [text for text, _ in sapi.said] == ["two"]


def test_voice_id_and_speed_reach_the_next_request_and_none_keeps_the_voice():
    opener = Opener()
    e = eleven_speaker(opener)
    a = announcer(eleven=e)

    a.apply(VoiceProfile(engine="elevenlabs", eleven_voice_id=None, eleven_speed=1.1))
    say(a, "Hello there.")
    assert e.wait_idle(2)
    assert "/voice_abc/stream" in opener.requests[0].full_url
    assert json.loads(opener.requests[0].data)["voice_settings"]["speed"] == 1.1

    a.apply(VoiceProfile(engine="elevenlabs", eleven_voice_id="voice_new", eleven_speed=0.8))
    say(a, "Hello again.")
    assert e.wait_idle(2) and len(opener.requests) == 2
    assert "/voice_new/stream" in opener.requests[1].full_url
    assert json.loads(opener.requests[1].data)["voice_settings"]["speed"] == 0.8


def test_a_cached_line_is_not_replayed_in_the_old_voice_after_a_voice_change():
    opener = Opener()
    e = eleven_speaker(opener)
    a = announcer(eleven=e)

    say(a, "Canceled.")
    assert e.wait_idle(2) and len(opener.requests) == 1
    a.apply(VoiceProfile(engine="elevenlabs", eleven_voice_id="voice_new"))
    say(a, "Canceled.")
    assert e.wait_idle(2) and len(opener.requests) == 2
    assert "/voice_new/stream" in opener.requests[1].full_url


def test_a_line_in_flight_finishes_when_the_voice_changes():
    outputs, sapi = [], FakeSapi()
    opener = Opener(lambda: FakeResponse([b"x" * 2] * 30))
    e = eleven_speaker(opener, outputs, fallback=sapi, per_write_s=0.02)
    a = announcer(eleven=e, sapi=sapi)
    a.apply(VoiceProfile(engine="elevenlabs", eleven_voice_id="voice_abc"))

    say(a, "A long answer that is still playing.")
    wait_for(lambda: outputs and outputs[0].written)
    a.apply(VoiceProfile(engine="sapi", sapi_voice="Zira"))

    assert e.wait_idle(5)
    assert not outputs[0].aborted and len(outputs[0].written) == 30
    assert sapi.said == []


def test_apply_with_nvda_never_calls_nvda_beyond_normal_routing():
    nvda, sapi, opener = FakeNvda(), FakeSapi(), Opener()
    e = eleven_speaker(opener)
    a = announcer(eleven=e, sapi=sapi, nvda=nvda, running=True)

    a.apply(VoiceProfile(engine="nvda", eleven_voice_id="voice_new", eleven_speed=0.9, sapi_rate=3))
    assert nvda.calls == []  # apply itself touches nothing on NVDA
    say(a, "Hi.")

    assert nvda.calls == ["available", "speak"]  # exactly what routing does for any line
    assert nvda.said == [("Hi.", False)]
    assert sapi.rates == [3] and sapi.chosen == []  # SAPI is ready as the fallback; NVDA is never touched
    assert opener.requests == []


def test_apply_sets_the_sapi_voice_and_rate_only_when_given():
    sapi = FakeSapi()
    a = announcer(eleven=eleven_speaker(), sapi=sapi)

    a.apply(VoiceProfile(engine="sapi", sapi_voice="Zira", sapi_rate=2))
    assert sapi.chosen == ["Zira"] and sapi.rates == [2]

    a.apply(VoiceProfile(engine="sapi", sapi_voice=None, sapi_rate=None))
    assert sapi.chosen == ["Zira"] and sapi.rates == [2]


def test_a_failing_sapi_setter_does_not_stop_apply():
    sapi = FakeSapi(set_rate_error=RuntimeError("COM busy"))
    a = announcer(eleven=eleven_speaker(), sapi=sapi)
    a.apply(VoiceProfile(engine="sapi", sapi_voice="Zira", sapi_rate=2))
    assert sapi.chosen == ["Zira"] and a.engine == "sapi"


def test_apply_builds_elevenlabs_without_network_and_hooks_braille(monkeypatch):
    monkeypatch.setenv("ARTEMIS_VOICE_ENGINE", "sapi")  # so __init__ builds nothing
    opener, outputs = Opener(), []
    real = speech.ElevenLabsSpeaker

    def make_output():
        outputs.append(FakeOutput())
        return outputs[-1]

    monkeypatch.setattr(speech, "ElevenLabsSpeaker",
                        lambda **kw: real(api_key=KEY, opener=opener, output=make_output, speed=1.0, **kw))
    a = announcer(eleven=None)
    assert a.eleven is None

    a.apply(VoiceProfile(engine="elevenlabs", eleven_voice_id="voice_built"))

    assert a.eleven is not None and a.eleven.voice_id == "voice_built"
    assert a.eleven.braille == a._braille and a.eleven.fallback is not None
    assert opener.requests == []  # building did no network I/O
    assert a.active_engine() == "elevenlabs"


def test_active_engine_with_and_without_nvda_and_with_no_key():
    # With a key and the default engine, ElevenLabs speaks whether or not NVDA runs.
    assert announcer(eleven=eleven_speaker(), running=True).active_engine() == "elevenlabs"
    assert announcer(eleven=eleven_speaker(), running=False).active_engine() == "elevenlabs"

    # No key: ElevenLabs is unavailable, so NVDA when it runs, else SAPI.
    no_key = eleven_speaker(api_key="")
    assert announcer(eleven=no_key, running=True).active_engine() == "nvda"
    assert announcer(eleven=no_key, running=False).active_engine() == "sapi"

    # nvda and sapi engines never pick ElevenLabs, key or not.
    for engine in ("nvda", "sapi"):
        a = announcer(eleven=eleven_speaker(), running=True)
        a.apply(VoiceProfile(engine=engine))
        assert a.active_engine() == "nvda"
        a = announcer(eleven=eleven_speaker(), running=False)
        a.apply(VoiceProfile(engine=engine))
        assert a.active_engine() == "sapi"

    # Nothing can speak: NVDA running but its DLL unavailable, or no voice at all.
    assert announcer(eleven=no_key, nvda=FakeNvda(available=False), running=True).active_engine() == "none"
    assert announcer(eleven=no_key, sapi=FakeSapi(available=False), running=False).active_engine() == "none"


def test_apply_records_the_working_cue_choice():
    a = announcer(eleven=eleven_speaker())
    assert a.working_cue_earcon is False
    a.apply(VoiceProfile(engine="sapi", working_cue=True))
    assert a.working_cue_earcon is True
    a.apply(VoiceProfile(engine="sapi", working_cue=False))
    assert a.working_cue_earcon is False
