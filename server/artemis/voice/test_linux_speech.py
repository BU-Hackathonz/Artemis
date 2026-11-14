"""Linux speech tests: the platform is patched to linux; spd-say, HTTP and the audio device are fakes."""

import asyncio
import subprocess
import urllib.error

import pytest

from artemis.voice import speech
from artemis.voice.speech import Announcer, ElevenLabsSpeaker, SpeechDispatcherSpeaker, fallback_notice, orca_running

KEY = "sk_test_never_show_this_1234567890"


@pytest.fixture(autouse=True)
def linux(monkeypatch, tmp_path):  # tmp_path first: pytest builds it with the real platform
    monkeypatch.setattr(speech.sys, "platform", "linux")


class FakeChild:
    def __init__(self, argv, **kwargs):
        self.argv, self.kwargs, self.done, self.killed = argv, kwargs, False, False

    def poll(self):
        return 0 if (self.done or self.killed) else None

    def kill(self):
        self.killed = True


class FakePopen:
    def __init__(self):
        self.children = []

    def __call__(self, argv, **kwargs):
        child = FakeChild(argv, **kwargs)
        self.children.append(child)
        return child

    def spoken(self):
        return [c.argv[-1] for c in self.children if "--wait" in c.argv]


def offline(popen=None, installed=True):
    popen = popen or FakePopen()
    return SpeechDispatcherSpeaker(which=lambda name: "/usr/bin/spd-say" if installed else None, popen=popen), popen


class NoNvda:
    name = "nvda"

    def available(self):
        raise AssertionError("NVDA is never consulted on Linux")


class FakeResponse:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def read(self, n):
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        pass


class FakeOutput:
    def __init__(self):
        self.aborted = False

    def start(self):
        pass

    def write(self, chunk):
        pass

    def stop(self):
        pass

    def abort(self):
        self.aborted = True

    def close(self):
        pass


def eleven(opener):
    return ElevenLabsSpeaker(api_key=KEY, voice_id="voice_abc", opener=opener, output=FakeOutput)


# ----- router -----


def test_linux_order_is_elevenlabs_then_offline_then_silence():
    spd, _ = offline()
    e = eleven(lambda req, t: FakeResponse([b"ok"]))
    a = Announcer(nvda=NoNvda(), sapi=spd, is_nvda_running=speech.nvda_running, eleven=e)
    assert speech.nvda_running() is False
    assert a.current() is e
    e.gave_up = True
    assert a.current() is spd and a.status() == (True, None)
    gone, _ = offline(installed=False)
    a = Announcer(nvda=NoNvda(), sapi=gone, is_nvda_running=speech.nvda_running, eleven=e)
    assert a.current().name == "none" and a.status() == (False, None)


def test_default_local_voice_on_linux_is_speech_dispatcher(monkeypatch):
    monkeypatch.setattr(speech.shutil, "which", lambda name: None)
    a = Announcer(nvda=NoNvda(), is_nvda_running=lambda: False)
    assert isinstance(a.sapi, SpeechDispatcherSpeaker)


# ----- the offline speaker -----


def test_spd_say_gets_argv_only_and_dashes_are_text():
    spd, popen = offline()
    spd.speak("-rf is not an option here", interrupt=False)
    child = popen.children[0]
    assert child.argv == ["/usr/bin/spd-say", "--wait", "--", "-rf is not an option here"]
    assert "shell" not in child.kwargs and child.kwargs["stdin"] == subprocess.DEVNULL


def test_busy_is_exact_while_the_line_plays():
    spd, popen = offline()
    assert not spd.busy()
    spd.speak("main.py ran successfully.", interrupt=False)
    assert spd.busy() and spd.speaking() and spd.name == "speechd"
    popen.children[0].done = True
    assert not spd.busy() and not spd.speaking()


def test_cancel_stops_speech_dispatcher_and_kills_our_child():
    spd, popen = offline()
    spd.speak("A long answer.", interrupt=False)
    spd.cancel()
    assert popen.children[0].killed and not spd.busy()
    assert popen.children[1].argv == ["/usr/bin/spd-say", "--cancel"]


def test_high_line_cuts_off_the_current_one():
    spd, popen = offline()
    spd.speak("A long answer.", interrupt=False)
    spd.speak("main.py failed with exit code 1.", interrupt=True)
    assert popen.children[0].killed
    assert popen.spoken() == ["A long answer.", "main.py failed with exit code 1."]


def test_push_to_talk_stop_speech_cancels_the_offline_voice():
    spd, popen = offline()
    a = Announcer(nvda=NoNvda(), sapi=spd, is_nvda_running=lambda: False, eleven=eleven(lambda r, t: urllib.error.URLError("x")))
    a.eleven.gave_up = True
    asyncio.run(a.announce("A long answer.", "normal"))
    asyncio.run(a.stop_speech())
    assert popen.children[0].killed


# ----- fallback and wording -----


def test_a_failed_elevenlabs_line_is_spoken_offline():
    spd, popen = offline()

    def opener(request, timeout):
        raise urllib.error.URLError("offline")

    e = eleven(opener)
    Announcer(nvda=NoNvda(), sapi=spd, is_nvda_running=lambda: False, eleven=e)
    e.fallback = speech._Fallback(Announcer(nvda=NoNvda(), sapi=spd, is_nvda_running=lambda: False, eleven=e))
    e.speak("Created main.py, 1 line.", interrupt=False)
    assert e.wait_idle(2)
    assert popen.spoken() == ["Created main.py, 1 line."]


def test_linux_never_names_windows_sapi_or_nvda():
    notice = fallback_notice()
    assert notice == "My natural voice isn't reachable, so I'm using the offline voice."
    for word in ("Windows", "SAPI", "NVDA"):
        assert word not in notice


def test_three_failures_give_the_offline_notice():
    notices = []

    def opener(request, timeout):
        raise urllib.error.HTTPError("u", 503, "busy", {}, None)

    spd, _ = offline()
    e = eleven(opener)
    e.on_give_up = notices.append
    Announcer(nvda=NoNvda(), sapi=spd, is_nvda_running=lambda: False, eleven=e)
    for n in range(3):
        e.speak(f"line {n}", interrupt=False)
        assert e.wait_idle(2)
    assert notices == ["My natural voice isn't reachable, so I'm using the offline voice."]


# ----- Orca -----


def test_orca_detection_reads_process_names(tmp_path):
    (tmp_path / "123").mkdir()
    (tmp_path / "123" / "comm").write_text("bash\n")
    assert not orca_running(tmp_path)
    (tmp_path / "456").mkdir()
    (tmp_path / "456" / "comm").write_text("orca\n")
    assert orca_running(tmp_path)
