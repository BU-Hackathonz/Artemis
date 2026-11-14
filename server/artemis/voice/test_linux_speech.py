"""Linux speech tests: the platform is patched to linux; speech-dispatcher, HTTP and audio are fakes."""

import asyncio
import queue
import time
import urllib.error

import pytest

from artemis.voice import speech
from artemis.voice.speech import Announcer, ElevenLabsSpeaker, SpeechDispatcherSpeaker, fallback_notice, orca_running

KEY = "sk_test_never_show_this_1234567890"


@pytest.fixture(autouse=True)
def linux(monkeypatch, tmp_path):  # tmp_path first: pytest builds it with the real platform
    monkeypatch.setattr(speech.sys, "platform", "linux")


class FakeSpeechd:
    """A socket that answers like speech-dispatcher's SSIP server and logs every command in order."""

    def __init__(self):
        self.commands, self.spoken, self._out = [], [], queue.Queue()
        self._data, self._next_id, self.closed = None, 1, False

    def _reply(self, *lines):
        self._out.put("".join(line + "\r\n" for line in lines).encode())

    def sendall(self, data):
        for line in data.decode().split("\r\n")[:-1]:
            if self._data is not None:
                if line == ".":
                    msg_id, self._next_id = str(self._next_id), self._next_id + 1
                    self.spoken.append("\n".join(self._data))
                    self._data = None
                    self._reply(f"225-{msg_id}", "225 OK MESSAGE QUEUED")
                else:
                    self._data.append(line)
                continue
            self.commands.append(line)
            if line == "SPEAK":
                self._data = []
                self._reply("230 OK RECEIVING DATA")
            elif line.startswith("CANCEL"):
                self._reply("210 OK CANCELED")
            else:
                self._reply("208 OK")

    def event(self, code, msg_id, word):
        self._reply(f"{code}-{msg_id}", f"{code}-7", f"{code} {word}")

    def recv(self, n):
        return self._out.get()

    def close(self):
        self.closed = True
        self._out.put(b"")


def wait_for(check, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline
        time.sleep(0.005)


def speechd():
    fake = FakeSpeechd()
    return SpeechDispatcherSpeaker(connect=lambda: fake), fake


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
    def start(self):
        pass

    def write(self, chunk):
        pass

    def stop(self):
        pass

    def abort(self):
        pass

    def close(self):
        pass


def eleven(opener):
    return ElevenLabsSpeaker(api_key=KEY, voice_id="voice_abc", opener=opener, output=FakeOutput)


def offline_opener(request, timeout):
    raise urllib.error.URLError("offline")


# ----- router -----


def test_linux_order_is_elevenlabs_then_speechd_then_silence():
    spd, _ = speechd()
    e = eleven(lambda req, t: FakeResponse([b"ok"]))
    a = Announcer(nvda=NoNvda(), sapi=spd, is_nvda_running=speech.nvda_running, eleven=e)
    assert speech.nvda_running() is False
    assert a.current() is e
    e.gave_up = True
    assert a.current() is spd and a.status() == (True, None)

    def refuse():
        raise FileNotFoundError("speech-dispatcher")

    a = Announcer(nvda=NoNvda(), sapi=SpeechDispatcherSpeaker(connect=refuse), is_nvda_running=speech.nvda_running, eleven=e)
    assert a.current().name == "none" and a.status() == (False, None)


def test_default_local_voice_on_linux_is_speech_dispatcher():
    a = Announcer(nvda=NoNvda(), is_nvda_running=lambda: False)
    assert isinstance(a.sapi, SpeechDispatcherSpeaker) and a.sapi.name == "speechd"


# ----- the speech-dispatcher speaker -----


def test_connects_with_a_client_name_and_events_on():
    spd, fake = speechd()
    assert spd.available()
    assert fake.commands == ["SET self CLIENT_NAME user:artemis:voice", "SET self NOTIFICATION all on"]


def test_busy_and_speaking_follow_speech_dispatchers_events():
    spd, fake = speechd()
    spd.speak("main.py ran successfully.", interrupt=False)
    assert fake.spoken == ["main.py ran successfully."]
    assert spd.busy() and not spd.speaking()  # queued, not audible yet
    fake.event("701", "1", "BEGIN")
    wait_for(spd.speaking)
    fake.event("702", "1", "END")
    wait_for(lambda: not spd.busy() and not spd.speaking())


def test_a_high_line_cancels_only_our_messages_and_the_cancel_lands_first():
    spd, fake = speechd()
    spd.speak("A long answer.", interrupt=False)
    spd.speak("main.py failed with exit code 1.", interrupt=True)
    assert fake.commands[-3:] == ["SPEAK", "CANCEL self", "SPEAK"]  # answered before the next line goes in
    assert "CANCEL all" not in fake.commands  # Orca and every other client keep talking
    assert fake.spoken[-1] == "main.py failed with exit code 1."


def test_cancel_and_push_to_talk_stop_only_artemis():
    spd, fake = speechd()
    e = eleven(offline_opener)
    e.gave_up = True
    a = Announcer(nvda=NoNvda(), sapi=spd, is_nvda_running=lambda: False, eleven=e)
    asyncio.run(a.announce("A long answer.", "normal"))
    fake.event("701", "1", "BEGIN")
    wait_for(spd.speaking)
    asyncio.run(a.stop_speech())  # what WhisperVoice.start_listening does before the listening earcon
    assert fake.commands[-1] == "CANCEL self" and not spd.busy() and not spd.speaking()


def test_a_dot_line_is_escaped():
    spd, fake = speechd()
    spd.speak("first\n.hidden", interrupt=False)
    assert fake.spoken == ["first\n..hidden"]  # dot-stuffed, so it can't end the message early


def test_unreachable_speechd_is_unavailable_and_not_retried_every_line():
    calls = []
    now = [0.0]

    def refuse():
        calls.append(1)
        raise FileNotFoundError("speech-dispatcher")

    spd = SpeechDispatcherSpeaker(connect=refuse, clock=lambda: now[0])
    assert not spd.available() and not spd.available()
    assert len(calls) == 1
    now[0] += 6
    assert not spd.available() and len(calls) == 2


def test_a_lost_connection_reconnects_on_the_next_line():
    fakes = []

    def connect():
        fakes.append(FakeSpeechd())
        return fakes[-1]

    spd = SpeechDispatcherSpeaker(connect=connect)
    spd.speak("one", interrupt=False)
    fakes[0].close()  # speech-dispatcher restarted
    wait_for(lambda: spd._sock is None)
    spd.speak("two", interrupt=False)
    assert len(fakes) == 2 and fakes[1].spoken == ["two"]


# ----- fallback and wording -----


def test_a_failed_elevenlabs_line_is_spoken_by_speechd():
    spd, fake = speechd()
    e = eleven(offline_opener)
    a = Announcer(nvda=NoNvda(), sapi=spd, is_nvda_running=lambda: False, eleven=e)
    e.fallback = speech._Fallback(a)
    e.speak("Created main.py, 1 line.", interrupt=False)
    assert e.wait_idle(2)
    assert fake.spoken == ["Created main.py, 1 line."]


def test_linux_never_names_windows_sapi_or_nvda():
    notice = fallback_notice()
    assert notice == "My natural voice isn't reachable, so I'm using the offline voice."
    for word in ("Windows", "SAPI", "NVDA"):
        assert word not in notice


def test_three_failures_give_the_offline_notice():
    notices = []

    def opener(request, timeout):
        raise urllib.error.HTTPError("u", 503, "busy", {}, None)

    spd, _ = speechd()
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
