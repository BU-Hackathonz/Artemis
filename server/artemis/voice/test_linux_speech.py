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
    # the cancel is answered before the next line goes in, and the high line goes out as 'message'
    assert fake.commands[-4:] == ["SPEAK", "CANCEL self", "SET self PRIORITY message", "SPEAK"]
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



def test_each_artemis_priority_maps_to_its_speechd_level_and_is_set_only_on_change():
    spd, fake = speechd()
    a = Announcer(nvda=NoNvda(), sapi=spd, is_nvda_running=lambda: False, eleven=eleven(offline_opener))
    a.eleven.gave_up = True

    asyncio.run(a.announce("Created main.py, 1 line.", "normal"))
    asyncio.run(a.announce("Created utils.py, 2 lines.", "normal"))  # same level: no second SET
    asyncio.run(a.announce("Running main.py.", "low"))  # dropped: Artemis is still talking (busy)
    for msg_id in ("1", "2"):
        fake.event("702", msg_id, "END")
    wait_for(lambda: not spd.busy())
    asyncio.run(a.announce("Running main.py.", "low"))  # quiet now: goes out as 'notification'
    asyncio.run(a.announce("main.py failed with exit code 1.", "high"))
    levels = [c for c in fake.commands if c.startswith("SET self PRIORITY")]
    assert levels == ["SET self PRIORITY text", "SET self PRIORITY notification", "SET self PRIORITY message"]
    assert fake.spoken == ["Created main.py, 1 line.", "Created utils.py, 2 lines.", "Running main.py.",
                           "main.py failed with exit code 1."]



def test_a_silently_dropped_notification_never_leaves_busy_stuck():
    # Measured on Ubuntu: speech-dispatcher discards a notification while another client speaks and
    # sends no 702/703 for it. busy() must not wait for an event that never comes.
    spd, fake = speechd()
    spd.speak("Still working.", interrupt=False, priority="low")
    assert fake.spoken == ["Still working."] and not spd.busy()
    spd.speak("Created main.py, 1 line.", interrupt=False, priority="normal")
    assert spd.busy()  # a normal line is still tracked until its END

def test_windows_speakers_never_get_a_priority_argument():
    said = []

    class TwoArgs:
        name = "sapi"

        def available(self):
            return True

        def speak(self, text, interrupt):  # no priority parameter: must still work, unchanged
            said.append((text, interrupt))

        def cancel(self):
            pass

        def busy(self):
            return False

    a = Announcer(nvda=NoNvda(), sapi=TwoArgs(), is_nvda_running=lambda: False, eleven=eleven(offline_opener))
    a.eleven.gave_up = True
    asyncio.run(a.announce("Hello.", "high"))
    assert said == [("Hello.", True)]

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



def test_a_late_reply_from_a_dead_connection_never_answers_the_next_command():
    # Avery's #148 review: with one queue across connections, a reply that lands after a timeout but
    # before the socket closes answers the next connection's command, and every later reply is one behind.
    class Stalls(FakeSpeechd):
        def sendall(self, data):
            if data.strip() == b"SPEAK":
                self.commands.append("SPEAK")  # no reply in time
                return
            super().sendall(data)

        def close(self):
            self._reply("230 OK RECEIVING DATA")  # the late reply arrives just before the close
            super().close()

    first, second = Stalls(), FakeSpeechd()
    conns = [first, second]
    spd = SpeechDispatcherSpeaker(connect=lambda: conns.pop(0))
    spd.REPLY_TIMEOUT_S = 0.05
    spd.speak("lost line", interrupt=False)  # times out and drops the connection
    time.sleep(0.05)  # let the dead connection's reader deliver its late reply
    spd.speak("next line", interrupt=False)
    assert second.spoken == ["next line"]
    assert spd.busy()  # the 225 reply with the message id was read by its own command, not shifted
    assert spd._replies.empty()  # nothing left over to answer a later command


def test_racing_callers_open_only_one_connection():
    import threading

    opened = []

    def slow_connect():
        time.sleep(0.05)
        opened.append(FakeSpeechd())
        return opened[-1]

    spd = SpeechDispatcherSpeaker(connect=slow_connect)
    threads = [threading.Thread(target=spd.available) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(opened) == 1

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



def test_a_402_gives_up_at_once_and_says_so_once():
    # Measured on David's laptop: a free key gets HTTP 402 for library voices (Morgan) on every line.
    notices, calls = [], []

    def opener(request, timeout):
        calls.append(1)
        raise urllib.error.HTTPError("u", 402, "payment required", {}, None)

    spd, fake = speechd()
    e = eleven(opener)
    e.on_give_up = notices.append
    a = Announcer(nvda=NoNvda(), sapi=spd, is_nvda_running=lambda: False, eleven=e)
    e.fallback = speech._Fallback(a)
    e.speak("line one", interrupt=False)
    assert e.wait_idle(2)
    assert e.gave_up and len(calls) == 1 and notices == [fallback_notice()]
    assert fake.spoken == ["line one"] and a.current() is spd  # this line and every later one go offline


def test_a_temporary_failure_still_gets_three_tries():
    def opener(request, timeout):
        raise urllib.error.HTTPError("u", 503, "busy", {}, None)

    e = eleven(opener)
    Announcer(nvda=NoNvda(), sapi=speechd()[0], is_nvda_running=lambda: False, eleven=e)
    e.speak("line one", interrupt=False)
    assert e.wait_idle(2) and not e.gave_up


def test_linux_no_key_log_names_the_offline_voice(caplog, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    ElevenLabsSpeaker(api_key="", voice_id="voice_abc", fallback=None)
    assert "the offline voice" in caplog.text and "NVDA" not in caplog.text and "Windows" not in caplog.text

# ----- Orca -----


def test_orca_detection_reads_process_names(tmp_path):
    (tmp_path / "123").mkdir()
    (tmp_path / "123" / "comm").write_text("bash\n")
    assert not orca_running(tmp_path)
    (tmp_path / "456").mkdir()
    (tmp_path / "456" / "comm").write_text("orca\n")
    assert orca_running(tmp_path)
