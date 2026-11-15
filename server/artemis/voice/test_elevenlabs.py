"""ElevenLabs engine tests. The HTTP stream and the audio device are fakes: nothing goes on the network."""

import asyncio
import json
import logging
import threading
import time
import urllib.error

from artemis.voice.speech import (
    FALLBACK_NOTICE,
    _Fallback,
    Announcer,
    DEFAULT_VOICE_ID,
    PLACEHOLDER_VOICE,
    ElevenLabsSpeaker,
    RateStatus,
)

KEY = "sk_test_never_show_this_1234567890"


class FakeSpeaker:
    def __init__(self, name):
        self.name, self.said, self.canceled = name, [], 0

    def available(self):
        return True

    def speak(self, text, interrupt):
        self.said.append((text, interrupt))

    def cancel(self):
        self.canceled += 1

    def busy(self):
        return False


class FakeResponse:
    def __init__(self, chunks, fail_first=None):
        self.chunks, self.fail_first, self.closed = list(chunks), fail_first, False

    def read(self, n):
        if self.fail_first is not None:
            error, self.fail_first = self.fail_first, None
            raise error
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        self.closed = True


class FakeOutput:
    def __init__(self, per_write_s=0.0):
        self.per_write_s, self.written, self.aborted, self.stopped = per_write_s, [], False, False

    def start(self):
        pass

    def write(self, chunk):
        if self.aborted:
            raise RuntimeError("stream aborted")
        time.sleep(self.per_write_s)
        self.written.append(chunk)

    def stop(self):
        self.stopped = True

    def abort(self):
        self.aborted = True

    def close(self):
        pass


class Opener:
    def __init__(self, make_response):
        self.make_response, self.requests, self.responses = make_response, [], []

    def __call__(self, request, timeout_s):
        self.requests.append(request)
        response = self.make_response()
        if isinstance(response, Exception):
            raise response
        self.responses.append(response)
        return response


def eleven(opener, outputs=None, sapi=None, **kw):
    outputs = [] if outputs is None else outputs
    per_write = kw.pop("per_write_s", 0.0)

    def output():
        outputs.append(FakeOutput(per_write))
        return outputs[-1]

    return ElevenLabsSpeaker(api_key=KEY, voice_id="voice_abc", fallback=sapi or FakeSpeaker("sapi"),
                             opener=opener, output=output, **kw)


def ok_opener(chunks=(b"\x01\x00" * 10, b"\x02\x00" * 10)):
    return Opener(lambda: FakeResponse(chunks))


# ----- routing -----


class BrailleNvda(FakeSpeaker):
    def __init__(self):
        super().__init__("nvda")
        self.brailled = []

    def braille(self, text):
        self.brailled.append(text)


def test_elevenlabs_speaks_even_while_nvda_runs_and_nvda_keeps_braille():
    opener = ok_opener()
    nvda, sapi = BrailleNvda(), FakeSpeaker("sapi")
    e = eleven(opener, sapi=sapi)
    a = Announcer(nvda=nvda, sapi=sapi, is_nvda_running=lambda: True, eleven=e)
    asyncio.run(a.announce("main.py ran successfully.", "high"))
    assert e.wait_idle(2)
    assert a.current() is e and len(opener.requests) == 1
    assert nvda.said == [] and nvda.canceled == 0  # never a second voice, and NVDA's own reading is never cut
    assert nvda.brailled == ["main.py ran successfully."]  # the braille display still gets the line
    assert a.status() == (True, None)  # Artemis speaks through ElevenLabs, not NVDA


def test_choosing_nvda_or_sapi_keeps_nvda_first(monkeypatch):
    opener = ok_opener()
    for engine in ("nvda", "sapi"):
        monkeypatch.setenv("ARTEMIS_VOICE_ENGINE", engine)
        nvda, sapi = FakeSpeaker("nvda"), FakeSpeaker("sapi")
        a = Announcer(nvda=nvda, sapi=sapi, is_nvda_running=lambda: True, eleven=eleven(opener, sapi=sapi))
        asyncio.run(a.announce("main.py ran successfully.", "high"))
        assert nvda.said == [("main.py ran successfully.", True)] and sapi.said == []
    assert opener.requests == []


def test_without_nvda_the_order_is_elevenlabs_then_sapi():
    sapi = FakeSpeaker("sapi")
    e = eleven(ok_opener(), sapi=sapi)
    a = Announcer(nvda=FakeSpeaker("nvda"), sapi=sapi, is_nvda_running=lambda: False, eleven=e)
    assert a.current() is e and a.status() == (True, None)
    e.gave_up = True
    assert a.current() is sapi


def test_elevenlabs_is_the_default_and_without_a_key_it_is_nvda_then_sapi(monkeypatch):
    a = Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: False)
    assert a.engine == "elevenlabs" and a.eleven is not None and not a.eleven.available()
    assert a.current().name == "sapi"  # no key: today's behaviour
    a = Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: True)
    assert a.current().name == "nvda"
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    monkeypatch.setenv("ARTEMIS_ELEVEN_VOICE_ID", "voice_abc")
    a = Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: True)
    assert a.current().name == "elevenlabs"
    monkeypatch.setenv("ARTEMIS_VOICE_ENGINE", "loud")  # unknown: warns, keeps the default
    assert Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: True).engine == "elevenlabs"


def test_missing_key_or_voice_falls_back_to_sapi_with_one_warning(monkeypatch, caplog):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    e = ElevenLabsSpeaker(voice_id="voice_abc", fallback=FakeSpeaker("sapi"))
    assert not e.available() and "ELEVENLABS_API_KEY is not set" in caplog.text
    e = ElevenLabsSpeaker(api_key=KEY, voice_id=PLACEHOLDER_VOICE, fallback=FakeSpeaker("sapi"))
    assert not e.available()


def test_artemis_voice_is_the_default_and_the_env_overrides_it(monkeypatch):
    assert ElevenLabsSpeaker(api_key=KEY, fallback=FakeSpeaker("sapi")).voice_id == DEFAULT_VOICE_ID
    monkeypatch.setenv("ARTEMIS_ELEVEN_VOICE_ID", PLACEHOLDER_VOICE)  # the docs' placeholder means unset
    assert ElevenLabsSpeaker(api_key=KEY, fallback=FakeSpeaker("sapi")).voice_id == DEFAULT_VOICE_ID
    monkeypatch.setenv("ARTEMIS_ELEVEN_VOICE_ID", "voice_abc")
    e = ElevenLabsSpeaker(api_key=KEY, fallback=FakeSpeaker("sapi"))
    assert e.voice_id == "voice_abc" and e.available()


def test_with_only_a_key_elevenlabs_speaks_in_artemiss_voice(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    a = Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: False)
    assert a.current().name == "elevenlabs" and a.eleven.voice_id == DEFAULT_VOICE_ID


# ----- the request -----


def test_request_uses_the_settings_and_sends_the_key_only_in_the_header():
    opener = ok_opener()
    e = eleven(opener, speed=1.1, stability=0.4, model="eleven_flash_v2_5")
    e.speak("Hello.", interrupt=False)
    assert e.wait_idle(2)
    req = opener.requests[0]
    assert req.full_url.startswith("https://api.elevenlabs.io/v1/text-to-speech/voice_abc/stream")
    assert "output_format=pcm_22050" in req.full_url and KEY not in req.full_url
    assert req.get_header("Xi-api-key") == KEY
    body = json.loads(req.data)
    assert body["text"] == "Hello." and body["model_id"] == "eleven_flash_v2_5"
    assert body["voice_settings"] == {"stability": 0.4, "similarity_boost": 0.75, "use_speaker_boost": True, "speed": 1.1}


def test_audio_plays_chunk_by_chunk():
    outputs = []
    e = eleven(ok_opener((b"a" * 4, b"b" * 4, b"c" * 4)), outputs)
    e.speak("A sentence that is not cached.", interrupt=False)
    assert e.wait_idle(2)
    assert outputs[0].written == [b"a" * 4, b"b" * 4, b"c" * 4] and outputs[0].stopped
    assert e.last_first_audio_s is not None and not e.busy()


# ----- speed -----


def test_speed_is_clamped_and_steps_by_a_tenth():
    assert eleven(ok_opener(), speed=3.0).speed == 1.2
    assert eleven(ok_opener(), speed=0.1).speed == 0.7
    e = eleven(ok_opener(), speed=1.0)
    a = Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: False, eleven=e)
    assert asyncio.run(a.set_rate(1)) == RateStatus.FASTER and e.speed == 1.1
    assert asyncio.run(a.set_rate(1)) == RateStatus.FASTER and e.speed == 1.2
    assert asyncio.run(a.set_rate(1)) == RateStatus.AT_MAX and e.speed == 1.2
    for _ in range(5):
        asyncio.run(a.set_rate(-1))
    assert e.speed == 0.7 and asyncio.run(a.set_rate(-1)) == RateStatus.AT_MIN
    assert asyncio.run(a.set_rate(0)) == RateStatus.UNCHANGED


def test_faster_reaches_elevenlabs_even_with_nvda_but_never_nvda_itself(monkeypatch):
    e = eleven(ok_opener(), speed=1.0)
    a = Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: True, eleven=e)
    assert asyncio.run(a.set_rate(1)) == RateStatus.FASTER and e.speed == 1.1
    monkeypatch.setenv("ARTEMIS_VOICE_ENGINE", "nvda")
    a = Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: True, eleven=e)
    assert asyncio.run(a.set_rate(1)) == RateStatus.NVDA and e.speed == 1.1


def test_a_failed_line_falls_back_to_nvda_when_it_runs_and_cancel_spares_nvda_otherwise():
    nvda, sapi = BrailleNvda(), FakeSpeaker("sapi")
    running = [True]
    e = ElevenLabsSpeaker(api_key=KEY, voice_id="voice_abc", opener=Opener(lambda: urllib.error.URLError("offline")),
                          output=FakeOutput)
    a = Announcer(nvda=nvda, sapi=sapi, is_nvda_running=lambda: running[0], eleven=e)
    e.fallback = _Fallback(a)
    e.speak("Created main.py, 1 line.", interrupt=False)
    assert e.wait_idle(2)
    assert nvda.said == [("Created main.py, 1 line.", False)] and sapi.said == []  # never SAPI over NVDA
    e.cancel()
    assert nvda.canceled == 1  # it was NVDA saying our line, so stopping it is right
    e.cancel()
    assert nvda.canceled == 1  # nothing of ours left: NVDA's own reading is not touched
    running[0] = False
    e.speak("Hello.", interrupt=False)
    assert e.wait_idle(2) and sapi.said == [("Hello.", False)]


# ----- interrupt, cancel, busy -----


def test_interrupt_stops_the_current_line_at_once():
    outputs = []
    many = [b"x" * 2] * 50
    e = eleven(Opener(lambda: FakeResponse(list(many))), outputs, per_write_s=0.02)
    e.speak("A long answer that takes a while.", interrupt=False)
    deadline = time.monotonic() + 2
    while not e.busy() or not outputs or not outputs[0].written:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    e.speak("main.py failed with exit code 1.", interrupt=True)
    assert e.wait_idle(5)
    assert outputs[0].aborted and len(outputs[0].written) < 50
    assert len(outputs) == 2 and len(outputs[1].written) == 50


def test_cancel_stops_playback_drops_the_queue_and_the_sapi_fallback():
    outputs, sapi = [], FakeSpeaker("sapi")
    opener = Opener(lambda: FakeResponse([b"x" * 2] * 50))
    e = eleven(opener, outputs, sapi=sapi, per_write_s=0.02)
    e.speak("first line", interrupt=False)
    e.speak("second line", interrupt=False)
    deadline = time.monotonic() + 2
    while not outputs or not outputs[0].written:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    e.cancel()
    assert e.wait_idle(3)
    assert outputs[0].aborted and len(outputs) == 1  # the queued line never played
    assert all(r.closed for r in opener.responses)  # its prefetch was abandoned too
    assert sapi.canceled == 1 and not e.busy()


def test_low_lines_are_dropped_while_elevenlabs_plays():
    outputs = []
    e = eleven(Opener(lambda: FakeResponse([b"x" * 2] * 30)), outputs, per_write_s=0.02)
    a = Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: False, eleven=e)

    async def run():
        await a.announce("main.py ran successfully.", "high")
        await asyncio.sleep(0.15)
        await a.announce("Still working.", "low")  # dropped: the result is still playing

    asyncio.run(run())
    assert e.wait_idle(5) and len(outputs) == 1


def test_push_to_talk_stop_speech_cancels_elevenlabs():
    outputs = []
    e = eleven(Opener(lambda: FakeResponse([b"x" * 2] * 50)), outputs, per_write_s=0.02)
    a = Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: False, eleven=e)
    asyncio.run(a.announce("A long answer.", "normal"))
    deadline = time.monotonic() + 2
    while not outputs or not outputs[0].written:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    asyncio.run(a.stop_speech())  # what WhisperVoice.start_listening calls before the listening earcon
    assert outputs[0].aborted and e.wait_idle(3)


# ----- speech flow: queue, stale progress, prefetch -----


class SlowResponse(FakeResponse):
    """A response whose reads take a while: a slow first audio, or a stall mid-line."""

    def __init__(self, chunks, delays):
        super().__init__(chunks)
        self.delays = list(delays)

    def read(self, n):
        if self.delays:
            time.sleep(self.delays.pop(0))
        return super().read(n)


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_a_normal_line_waits_for_the_one_playing_instead_of_cutting_it():
    outputs = []
    e = eleven(Opener(lambda: FakeResponse([b"x" * 2] * 20)), outputs, per_write_s=0.01)
    a = Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: False, eleven=e)

    async def run():
        await a.announce("Created convert.py, 34 lines.", "normal")
        await asyncio.sleep(0.05)
        await a.announce("I made convert.py and it works.", "normal")

    asyncio.run(run())
    assert e.wait_idle(5)
    assert len(outputs) == 2 and not outputs[0].aborted
    assert [len(o.written) for o in outputs] == [20, 20]  # both lines played whole, one after the other


def test_a_newer_line_drops_a_progress_line_not_heard_yet():
    outputs, texts = [], []

    def make():
        if not texts:
            texts.append("low")
            return SlowResponse([b"l" * 2] * 5, [0.3])  # the progress line's audio is slow to start
        return FakeResponse([b"n" * 2] * 5)

    e = eleven(Opener(make), outputs, timeout_s=2.0)
    a = Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: False, eleven=e)

    async def run():
        await a.announce("Starting project celsius-to-fahrenheit.", "low")
        await asyncio.sleep(0.05)
        await a.announce("Create project celsius-to-fahrenheit with 1 file.", "normal")

    asyncio.run(run())
    assert e.wait_idle(5)
    played = b"".join(b"".join(o.written) for o in outputs)
    assert b"l" not in played and played.count(b"n") == 10  # the stale progress line was never heard


def test_a_queued_progress_line_is_dropped_when_a_newer_line_arrives():
    outputs = []
    e = eleven(Opener(lambda: FakeResponse([b"x" * 2] * 10)), outputs, per_write_s=0.02)
    e.speak("A normal line playing now.", interrupt=False)
    wait_for(lambda: outputs and outputs[0].written)
    e.speak("Running convert.py.", interrupt=False, priority="low")
    e.speak("convert.py ran successfully.", interrupt=False)
    assert e.wait_idle(5) and len(outputs) == 2


def test_the_next_line_is_fetched_while_this_one_plays():
    outputs = []
    opener = Opener(lambda: FakeResponse([b"x" * 2] * 30))
    e = eleven(opener, outputs, per_write_s=0.02)
    e.speak("first line", interrupt=False)
    e.speak("second line", interrupt=False)
    wait_for(lambda: outputs and outputs[0].written)
    wait_for(lambda: len(opener.requests) == 2)
    assert len(outputs[0].written) < 30  # the second request went out while the first line was still playing
    assert e.wait_idle(5) and [len(o.written) for o in outputs] == [30, 30]
    assert e.last_first_audio_s is not None and e.last_first_audio_s < 0.2  # its audio was already there


def test_an_interrupt_plays_the_next_line_at_once_even_if_the_stream_is_stalled():
    outputs = []
    first = {"made": False}

    def make():
        if not first["made"]:
            first["made"] = True
            return SlowResponse([b"a" * 2, b"b" * 2], [0, 5.0])  # one chunk, then a 5 s stall
        return FakeResponse([b"z" * 2])

    e = eleven(Opener(make), outputs, timeout_s=8.0)
    e.speak("A long answer.", interrupt=False)
    wait_for(lambda: outputs and outputs[0].written)  # playing; the worker is waiting on the stalled stream
    started = time.monotonic()
    e.speak("Canceled.", interrupt=True)
    wait_for(lambda: len(outputs) > 1 and outputs[1].written)
    assert time.monotonic() - started < 0.5
    assert outputs[1].written == [b"zz"]
    assert e.wait_idle(2)


def test_an_interrupt_during_the_first_chunk_wait_plays_the_next_line_at_once():
    outputs = []
    calls = []

    def make():
        calls.append(1)
        return SlowResponse([b"a" * 2], [5.0]) if len(calls) == 1 else FakeResponse([b"z" * 2])

    e = eleven(Opener(make), outputs, timeout_s=8.0)
    e.speak("A long answer.", interrupt=False)
    wait_for(lambda: calls)
    started = time.monotonic()
    e.speak("Canceled.", interrupt=True)
    wait_for(lambda: outputs and outputs[-1].written == [b"zz"])
    assert time.monotonic() - started < 0.5
    assert e.wait_idle(2)


def test_a_stall_after_the_first_audio_does_not_cut_the_line():
    outputs = []
    # The first-audio deadline is 0.2 s; a 0.4 s stall mid-line is a slow network, not a failure.
    e = eleven(Opener(lambda: SlowResponse([b"a" * 2, b"b" * 2, b"c" * 2], [0, 0.4])), outputs, timeout_s=0.2)
    e.speak("A long answer.", interrupt=False)
    assert e.wait_idle(5)
    assert outputs[0].written == [b"aa", b"bb", b"cc"]


def test_a_line_cut_mid_stream_is_logged_with_its_reason(caplog):
    class Breaks(FakeResponse):
        def read(self, n):
            if len(self.chunks) == 1:
                raise ConnectionResetError("peer reset")
            return super().read(n)

    outputs = []
    e = eleven(Opener(lambda: Breaks([b"a" * 2, b"b" * 2])), outputs)
    with caplog.at_level(logging.WARNING):
        e.speak("A long answer.", interrupt=False)
        assert e.wait_idle(5)
    assert outputs[0].written == [b"aa"]
    assert "stream stopped mid-line (ConnectionResetError)" in caplog.text and KEY not in caplog.text


def test_a_line_spoken_by_the_local_voice_never_overlaps_the_next_one():
    class SlowLocal(FakeSpeaker):
        def __init__(self):
            super().__init__("speechd")
            self.until = 0.0

        def speak(self, text, interrupt):
            super().speak(text, interrupt)
            self.until = time.monotonic() + 0.3

        def busy(self):
            return time.monotonic() < self.until

    local = SlowLocal()
    results = [urllib.error.URLError("offline"), None]
    started = []

    def make():
        r = results.pop(0)
        return FakeResponse([b"x" * 2]) if r is None else r

    def output():
        started.append(local.busy())
        return FakeOutput()

    e = ElevenLabsSpeaker(api_key=KEY, voice_id="voice_abc", fallback=local, opener=Opener(make), output=output)
    e.speak("Created main.py, 1 line.", interrupt=False)
    e.speak("main.py ran successfully.", interrupt=False)
    assert e.wait_idle(5)
    assert local.said == [("Created main.py, 1 line.", False)]
    assert started == [False]  # ElevenLabs waited until the local voice was done


def test_a_failed_line_with_no_local_voice_is_logged_with_its_reason(caplog):
    class Gone(FakeSpeaker):
        def available(self):
            return False

    e = eleven(Opener(lambda: urllib.error.HTTPError("u", 503, "busy", {}, None)), sapi=Gone("sapi"))
    with caplog.at_level(logging.WARNING):
        e.speak("Hello.", interrupt=False)
        assert e.wait_idle(2)
    assert "ElevenLabs failed (HTTP 503) and no local voice is available" in caplog.text


def test_the_announcer_tells_elevenlabs_the_priority():
    seen = []
    e = eleven(ok_opener())
    e.speak = lambda text, interrupt, priority=None: seen.append((text, interrupt, priority))
    a = Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: False, eleven=e)
    for priority in ("high", "normal", "low"):
        asyncio.run(a.announce(f"a {priority} line", priority))
    assert seen == [("a high line", True, "high"), ("a normal line", False, "normal"), ("a low line", False, "low")]


def test_the_default_model_is_flash_for_a_fast_first_audio(monkeypatch):
    monkeypatch.delenv("ARTEMIS_ELEVEN_MODEL", raising=False)
    opener = ok_opener()
    e = eleven(opener)
    assert e.model == "eleven_flash_v2_5"
    e.speak("Hello.", interrupt=False)
    assert e.wait_idle(2)
    request = opener.requests[0]
    assert request.full_url.split("?")[0].endswith("/stream") and json.loads(request.data)["model_id"] == "eleven_flash_v2_5"


# ----- fallback and the circuit breaker -----


def test_slow_first_chunk_falls_back_to_sapi_for_that_line(caplog):
    sapi = FakeSpeaker("sapi")
    e = eleven(Opener(lambda: FakeResponse([b"x"], fail_first=TimeoutError("timed out"))), sapi=sapi)
    e.speak("Created main.py, 1 line.", interrupt=False)
    assert e.wait_idle(2)
    assert sapi.said == [("Created main.py, 1 line.", False)]
    assert "no audio within the timeout" in caplog.text and not e.gave_up


def test_fallback_runs_on_the_event_loop_thread():
    sapi = FakeSpeaker("sapi")
    seen = []
    sapi.speak = lambda text, interrupt: seen.append((text, threading.current_thread() is threading.main_thread()))
    e = eleven(Opener(lambda: urllib.error.URLError("offline")), sapi=sapi)

    async def run():
        e.speak("Hello.", interrupt=False)
        await asyncio.to_thread(e.wait_idle, 2)
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert seen == [("Hello.", True)]  # SAPI (COM) is only ever called from the loop's thread


def test_three_failures_in_a_row_switch_to_sapi_and_say_so_once():
    sapi, notices = FakeSpeaker("sapi"), []
    e = eleven(Opener(lambda: urllib.error.HTTPError("u", 503, "busy", {}, None)), sapi=sapi,
               on_give_up=notices.append)
    a = Announcer(nvda=FakeSpeaker("nvda"), sapi=sapi, is_nvda_running=lambda: False, eleven=e)
    for n in range(3):
        e.speak(f"line {n}", interrupt=False)
        assert e.wait_idle(2)
    assert e.gave_up and not e.available() and a.current() is sapi
    assert notices == [FALLBACK_NOTICE]
    assert [t for t, _ in sapi.said] == ["line 0", "line 1", "line 2"]


def test_a_success_resets_the_failure_count():
    results = [urllib.error.URLError("x"), urllib.error.URLError("x"), None, urllib.error.URLError("x")]

    def make():
        r = results.pop(0)
        return FakeResponse([b"ok"]) if r is None else r

    e = eleven(Opener(make))
    for n in range(4):
        e.speak(f"line {n}", interrupt=False)
        assert e.wait_idle(2)
    assert not e.gave_up


# ----- cache and privacy -----


def test_only_fixed_short_lines_are_cached():
    opener = ok_opener()
    e = eleven(opener)
    for _ in range(2):
        e.speak("Canceled.", interrupt=False)
        assert e.wait_idle(2)
    assert len(opener.requests) == 1  # the second one played from memory
    for _ in range(2):
        e.speak("print('my secret code')", interrupt=False)
        assert e.wait_idle(2)
    assert len(opener.requests) == 3  # never cached
    e.step_speed(1)
    e.speak("Canceled.", interrupt=False)
    assert e.wait_idle(2) and len(opener.requests) == 4  # cached per speed


def test_the_key_never_reaches_logs(caplog):
    caplog.set_level(logging.DEBUG)
    errors = [urllib.error.HTTPError("https://api.elevenlabs.io/x", 401, f"bad key {KEY}", {"xi-api-key": KEY}, None),
              urllib.error.URLError(f"fail {KEY}"), RuntimeError(f"boom {KEY}")]
    e = eleven(Opener(lambda: errors.pop(0)), on_give_up=lambda notice: None)
    for n in range(3):
        e.speak(f"line {n}", interrupt=False)
        assert e.wait_idle(2)
    assert e.gave_up and KEY not in caplog.text
    assert "HTTP 401" in caplog.text and KEY not in repr(e) and KEY not in FALLBACK_NOTICE


# ----- privacy -----


def test_text_sent_to_elevenlabs_is_redacted_but_the_fallback_hears_it_whole(monkeypatch):
    # Answers, "Heard:" transcripts and error lines reach the speaker without passing the gateway.
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    leaked = "sk-ant-api03-" + "x" * 40
    opener = ok_opener()
    e = eleven(opener)
    e.speak(f"Heard: my key is {KEY} and the token={leaked}.", interrupt=False)
    assert e.wait_idle(2)
    sent = opener.requests[0].data.decode("utf-8")
    assert KEY not in sent and leaked not in sent
    assert "[redacted]" in sent and "Heard: my key is" in sent

    sapi = FakeSpeaker("sapi")
    failing = eleven(Opener(lambda: urllib.error.URLError("offline")), sapi=sapi)
    failing.speak(f"The task failed. {KEY}", interrupt=False)
    assert failing.wait_idle(2)
    assert [t for t, _ in sapi.said] == [f"The task failed. {KEY}"]  # local speakers get the line as it was


def test_give_up_notice_is_logged_when_the_core_has_no_hook(caplog):
    e = eleven(Opener(lambda: urllib.error.HTTPError("u", 503, "busy", {}, None)))
    with caplog.at_level(logging.WARNING):
        for n in range(3):
            e.speak(f"line {n}", interrupt=False)
            assert e.wait_idle(2)
    assert e.gave_up and FALLBACK_NOTICE in caplog.text


# ----- a dropped (abandoned) line never plays -----


def test_a_progress_line_dropped_while_the_local_voice_speaks_never_plays():
    class Local(FakeSpeaker):
        def __init__(self):
            super().__init__("local")
            self.release, self.polls = threading.Event(), 0

        def busy(self):
            self.polls += 1
            return not self.release.is_set()

    local, outputs = Local(), []
    results = [urllib.error.URLError("offline"), b"b", b"c"]

    def make():
        r = results.pop(0)
        return r if isinstance(r, Exception) else FakeResponse([r * 2])

    e = eleven(Opener(make), outputs, sapi=local)
    e.speak("A normal line.", interrupt=False)  # fails: the local voice is handed it
    wait_for(lambda: local.said)
    e.speak("A progress line.", interrupt=False, priority="low")  # downloaded, then held for the local voice
    wait_for(lambda: local.polls > 0)
    e.speak("A newer normal line.", interrupt=False)  # drops the progress line; no cancel, gen is unchanged
    local.release.set()
    assert e.wait_idle(5)
    played = b"".join(b"".join(o.written) for o in outputs)
    assert b"b" not in played and played == b"cc"


def test_a_cached_line_dropped_while_the_local_voice_speaks_never_plays():
    class Local(FakeSpeaker):
        def __init__(self):
            super().__init__("local")
            self.release, self.polls = threading.Event(), 0

        def busy(self):
            self.polls += 1
            return not self.release.is_set()

    local, outputs = Local(), []
    e = eleven(Opener(lambda: FakeResponse([b"zz"])), outputs, sapi=local)
    e._cache[e._cache_key("A progress line.")] = b"bb"
    e._handed = threading.Event()
    e._handed.set()  # a line was handed to the local voice, which is still speaking
    e.speak("A progress line.", interrupt=False, priority="low")
    wait_for(lambda: local.polls > 0)
    e.speak("A newer normal line.", interrupt=False)
    local.release.set()
    assert e.wait_idle(5)
    assert [b"".join(o.written) for o in outputs] == [b"zz"]


def test_play_stops_between_chunks_when_the_line_is_abandoned():
    e = eleven(Opener(lambda: FakeResponse([])))
    from artemis.voice.speech import _Line

    line = _Line(0, "x", "low")

    def chunks():
        yield b"aa"
        line.abandoned.set()
        yield b"bb"

    out = FakeOutput()
    e._output = lambda: out
    assert e._play(chunks(), 0, line) is None
    assert out.written == [b"aa"] and not e.speaking()


def test_a_fallback_line_canceled_before_the_loop_runs_it_is_not_spoken():
    local = FakeSpeaker("local")
    e = eleven(Opener(lambda: urllib.error.URLError("offline")), sapi=local)

    async def run():
        e.speak("A normal line.", interrupt=False)
        wait_for(lambda: e._handed is not None)  # blocks the loop: the scheduled speak() has not run
        handed = e._handed
        e.cancel()
        await asyncio.sleep(0.1)
        return handed

    handed = asyncio.run(run())
    assert local.said == [] and handed.is_set()
def _hold_setup(busy_fn, monkeypatch=None):
    local = FakeSpeaker("sapi")
    local.busy = busy_fn
    e = eleven(Opener(lambda: urllib.error.URLError("offline")), sapi=local)
    return e


def test_hold_for_fallback_asks_busy_on_the_loop_thread():
    calls = []
    until = [time.monotonic() + 0.2]

    def busy():
        calls.append(threading.current_thread() is threading.main_thread())
        return time.monotonic() < until[0]

    e = _hold_setup(busy)

    async def run():
        e._loop = asyncio.get_running_loop()
        e._handed = threading.Event()
        e._handed.set()
        gen = e._gen
        t0 = time.monotonic()
        await asyncio.to_thread(e._hold_for_fallback, gen)
        return time.monotonic() - t0

    waited = asyncio.run(run())
    assert calls and all(calls)  # COM-owning thread only
    assert waited >= 0.15


def test_hold_for_fallback_keeps_waiting_a_bounded_time_when_busy_raises(monkeypatch):
    import artemis.voice.speech as sp

    monkeypatch.setattr(sp, "FALLBACK_UNSURE_WAIT_S", 0.3)
    n = []

    def busy():
        n.append(1)
        raise OSError("com error")

    e = _hold_setup(busy)

    async def run():
        e._loop = asyncio.get_running_loop()
        e._handed = threading.Event()
        e._handed.set()
        t0 = time.monotonic()
        await asyncio.to_thread(e._hold_for_fallback, e._gen)
        return time.monotonic() - t0

    waited = asyncio.run(run())
    assert 0.25 <= waited < 2.0  # did not return at once, did not wait the full 30 s
    assert len(n) > 1


def test_hold_for_fallback_without_a_loop_asks_directly():
    seen = []
    e = _hold_setup(lambda: seen.append(1) or False)
    e._handed = threading.Event()
    e._handed.set()
    e._hold_for_fallback(e._gen)
    assert seen == [1]


# ----- interrupts on Linux: the stream is aborted only by the thread that writes it -----


class ThreadOutput(FakeOutput):
    """Records which thread aborted it. ALSA breaks when another thread aborts a stream mid-write."""

    def __init__(self, per_write_s=0.0):
        super().__init__(per_write_s)
        self.writer = self.aborter = None

    def write(self, chunk):
        self.writer = threading.get_ident()
        super().write(chunk)

    def abort(self):
        self.aborter = threading.get_ident()
        super().abort()


def test_an_interrupt_aborts_the_stream_on_the_worker_thread_not_the_caller():
    outputs = []

    def output():
        outputs.append(ThreadOutput(0.02))
        return outputs[-1]

    e = ElevenLabsSpeaker(api_key=KEY, voice_id="voice_abc", fallback=FakeSpeaker("sapi"),
                          opener=Opener(lambda: FakeResponse([b"x" * 2] * 50)), output=output)
    e.speak("A long answer that takes a while.", interrupt=False)
    wait_for(lambda: outputs and outputs[0].written)
    e.speak("Write main.py? Enter to approve, Escape to reject.", interrupt=True)
    assert e.wait_idle(5)
    first = outputs[0]
    assert first.aborted and first.aborter == first.writer != threading.get_ident()
    assert len(outputs) == 2 and len(outputs[1].written) == 50  # the approval played whole


class BrokenOutput(FakeOutput):
    def write(self, chunk):
        raise OSError("ALSA: mmap error")


def test_a_line_whose_playback_fails_is_said_by_the_local_voice_not_dropped():
    sapi = FakeSpeaker("sapi")
    e = ElevenLabsSpeaker(api_key=KEY, voice_id="voice_abc", fallback=sapi,
                          opener=ok_opener(), output=BrokenOutput)
    e.speak("Write main.py? Enter to approve, Escape to reject.", interrupt=True)
    assert e.wait_idle(3)
    wait_for(lambda: sapi.said)
    assert sapi.said == [("Write main.py? Enter to approve, Escape to reject.", False)]
