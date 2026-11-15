"""WhisperVoice tests with a fake microphone and a fake model. Needs the voice group (numpy)."""

import asyncio

import pytest

np = pytest.importorskip("numpy")  # skip, don't fail collection, without the voice group

from artemis.voice.listen import MAX_SECONDS, RATE, VoiceError, WhisperVoice  # noqa: E402
from artemis.voice.text import NATURAL_LEAD

LIST_PROMPT = "Hey Artemis, Artemis, Python, main.py"  # main's #98 prompt, still the default
SETTINGS = ("ARTEMIS_WHISPER_MODEL", "ARTEMIS_WHISPER_BEAM", "ARTEMIS_PTT_POST_ROLL", "ARTEMIS_WHISPER_PROMPT")


@pytest.fixture(autouse=True)
def default_settings(monkeypatch):
    """The demo window may have opt-in settings set (demo.md setup); every test starts from the defaults."""
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)


class FakeStream:
    def __init__(self, callback, audio):
        self.callback, self.audio = callback, audio
        self.closed = False

    def start(self):
        # Deliver the "recorded" audio in 1600-frame blocks, like PortAudio would.
        for i in range(0, len(self.audio), 1600):
            block = self.audio[i : i + 1600].reshape(-1, 1)
            self.callback(block, len(block), None, None)

    def stop(self):
        pass

    def close(self):
        self.closed = True


class Seg:
    def __init__(self, text, no_speech_prob=0.02):
        self.text, self.no_speech_prob = text, no_speech_prob


class FakeModel:
    def __init__(self, text, segs=None):
        self.text, self.segs, self.calls = text, segs, []

    def transcribe(self, audio, **kw):
        self.calls.append((len(audio), kw))
        return iter(self.segs or [Seg(" " + w) for w in self.text.split("|")]), None


def speech_like(seconds, level=0.05):
    """A 220 Hz tone at about speech level, so the silence gate lets it through."""
    t = np.arange(int(RATE * seconds)) / RATE
    return (np.sin(2 * np.pi * 220 * t) * level * np.sqrt(2)).astype(np.float32)


class FakeAnnouncer:
    def __init__(self, log):
        self.log = log

    async def stop_speech(self):
        self.log.append("stop_speech")


def make(seconds, text="Artemis,| create a Python program", earcon_s=0.0, log=None, audio=None, segs=None, post_roll=None):
    audio = speech_like(seconds) if audio is None else audio
    streams = []
    log = [] if log is None else log

    def factory(cb):
        log.append("mic_open")
        streams.append(FakeStream(cb, audio))
        return streams[-1]

    def earcon(name):
        log.append(name)
        return earcon_s

    model = FakeModel(text, segs)
    v = WhisperVoice(announcer=FakeAnnouncer(log), model=model, stream_factory=factory, earcon=earcon, post_roll=post_roll)
    return v, model, streams


def test_defaults_are_mains_speech_settings():
    # Avery's call for #101: today's defaults stay; beam 5, post-roll and the natural prompt are opt-in.
    log = []
    v, model, streams = make(1, log=log)
    closed_at_earcon = []
    v._earcon = lambda name: (log.append(name), closed_at_earcon.append(streams[0].closed if streams else None), 0.0)[2]
    assert (v.model_name, v.beam_size, v.post_roll, v.natural_prompt) == ("small.en", 1, 0.0, False)
    asyncio.run(v.start_listening())
    assert asyncio.run(v.stop_listening("cr_1")) == "create a Python program"
    n, kw = model.calls[0]
    assert n == RATE and kw["beam_size"] == 1 and kw["initial_prompt"] == LIST_PROMPT
    assert log == ["stop_speech", "listening", "mic_open", "stopped"]
    assert closed_at_earcon == [None, False]  # as on main: "stopped" plays, then the mic closes at once
    v.set_vocabulary(["LoginForm"])
    assert v.prompt == LIST_PROMPT + ", LoginForm"


def test_default_release_never_waits():
    # No post-roll: a release with nothing to transcribe finishes without a single await.
    v, _, streams = make(1, audio=np.zeros(RATE, np.float32))
    asyncio.run(v.start_listening())
    stop = v.stop_listening("cr_1")
    with pytest.raises(StopIteration):
        stop.send(None)
    assert streams[0].closed and v._tail is None


def test_mic_stays_open_briefly_after_release_and_the_stopped_earcon_is_never_recorded(monkeypatch):
    # Opt-in. David's hardware run lost the last words ("and run it"): people let go while still speaking.
    monkeypatch.setenv("ARTEMIS_PTT_POST_ROLL", "0.35")
    log = []
    v, model, streams = make(1, log=log)
    assert v.post_roll == 0.35
    heard_after_close = []
    v._earcon = lambda name: (log.append(name), heard_after_close.append(streams[0].closed if streams else None), 0.0)[2]
    tail = speech_like(0.3)

    async def hold():
        await v.start_listening()
        stop = asyncio.create_task(v.stop_listening("cr_1"))  # key up
        await asyncio.sleep(0.01)
        assert "stopped" not in log and not streams[0].closed  # still recording the last word
        streams[0].callback(tail.reshape(-1, 1), len(tail), None, None)
        return await stop

    assert asyncio.run(hold()) == "create a Python program"
    assert model.calls[0][0] == int(1.3 * RATE)  # the words after release reached Whisper
    assert log == ["stop_speech", "listening", "mic_open", "stopped"]
    assert heard_after_close == [None, True]  # "stopped" played only once the mic was closed


def test_post_roll_comes_from_the_environment(monkeypatch, caplog):
    for raw, seconds in (("0.35", 0.35), (" 0.2 ", 0.2), ("1.0", 1.0), ("0", 0.0)):
        monkeypatch.setenv("ARTEMIS_PTT_POST_ROLL", raw)
        assert make(1)[0].post_roll == seconds, raw
    assert "ARTEMIS_PTT_POST_ROLL" not in caplog.text
    for bad in ("abc", "-0.1", "1.5", "nan", "inf", "350ms"):
        monkeypatch.setenv("ARTEMIS_PTT_POST_ROLL", bad)
        assert make(1)[0].post_roll == 0.0, bad
        assert f"ARTEMIS_PTT_POST_ROLL={bad!r}" in caplog.text



def test_busy_listening_covers_the_earcon_and_the_post_roll():
    # The core reads busy_listening before playing a result earcon (#127); a tone in this window is recorded.
    v, _, _ = make(1, earcon_s=0.1, post_roll=0.2)

    async def run():
        assert not v.busy_listening
        start = asyncio.create_task(v.start_listening())
        await asyncio.sleep(0.03)
        assert v.busy_listening and not v.listening  # the listening earcon plays; the mic isn't open yet
        await start
        assert v.busy_listening and v.listening
        stop = asyncio.create_task(v.stop_listening("cr_1"))
        await asyncio.sleep(0.05)
        assert v.busy_listening and not v.listening  # released, but the post-roll still records
        await stop
        assert not v.busy_listening and not v.listening

    asyncio.run(run())


def test_stub_busy_listening_matches_listening():
    from artemis.voice import StubVoice

    v = StubVoice(announcer=FakeAnnouncer([]))
    assert not v.busy_listening
    asyncio.run(v.start_listening())
    assert v.busy_listening
    asyncio.run(v.stop_listening("cr_1"))
    assert not v.busy_listening

def test_press_during_post_roll_closes_that_mic_first_and_skips_its_stopped_earcon():
    log = []
    v, _, streams = make(1, log=log, post_roll=5.0)

    async def double_press():
        await v.start_listening()
        first = asyncio.create_task(v.stop_listening("cr_1"))
        await asyncio.sleep(0.01)
        await asyncio.wait_for(v.start_listening(), 1.0)  # pressed again: no 5 s wait
        assert streams[0].closed and v.listening and len(streams) == 2
        text = await first
        v.post_roll = 0.0
        await v.stop_listening("cr_2")
        return text

    assert asyncio.run(double_press()) == "create a Python program"
    assert log == ["stop_speech", "listening", "mic_open", "stop_speech", "listening", "mic_open", "stopped"]
    assert streams[1].closed and not v.listening


def test_whisper_model_and_beam_come_from_the_environment(monkeypatch, caplog):
    v, model, _ = make(1)
    assert v.model_name == "small.en" and v.beam_size == 1
    monkeypatch.setenv("ARTEMIS_WHISPER_MODEL", "medium.en")
    monkeypatch.setenv("ARTEMIS_WHISPER_BEAM", "5")  # opt-in
    v, model, _ = make(1)
    assert v.model_name == "medium.en" and v.beam_size == 5
    asyncio.run(v.start_listening())
    asyncio.run(v.stop_listening("cr_1"))
    assert model.calls[0][1]["beam_size"] == 5 and model.calls[0][1]["vad_filter"] is True
    for bad in ("0", "five", "-2", "11"):
        monkeypatch.setenv("ARTEMIS_WHISPER_BEAM", bad)
        assert make(1)[0].beam_size == 1
    assert "ARTEMIS_WHISPER_BEAM" in caplog.text


def test_natural_prompt_is_opt_in(monkeypatch):
    for raw in ("list", "spoken", "1", ""):
        monkeypatch.setenv("ARTEMIS_WHISPER_PROMPT", raw)
        assert make(1)[0].prompt == LIST_PROMPT, raw
    monkeypatch.setenv("ARTEMIS_WHISPER_PROMPT", "natural")
    v, model, _ = make(1)
    assert v.natural_prompt and v.prompt == NATURAL_LEAD
    v.set_vocabulary(["LoginForm"])
    asyncio.run(v.start_listening())
    asyncio.run(v.stop_listening("cr_1"))
    assert model.calls[0][1]["initial_prompt"] == NATURAL_LEAD + " LoginForm."


def test_dropped_segments_are_logged_without_their_words(caplog):
    v, _, _ = make(2, segs=[Seg(" run main.py", 0.05), Seg(" and run it secret", 0.8)])
    asyncio.run(v.start_listening())
    assert asyncio.run(v.stop_listening("cr_1")) == "run main.py"
    assert "no_speech_prob 0.80" in caplog.text and "secret" not in caplog.text



def test_dropped_releases_log_their_reason_without_words(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="artemis.voice.listen")
    v, model, _ = make(0.1)  # a tap
    asyncio.run(v.start_listening())
    assert asyncio.run(v.stop_listening("cr_1")) == ""
    assert "Release dropped: too short" in caplog.text
    hiss = (np.random.default_rng(2).standard_normal(RATE * 2) * 0.0002).astype(np.float32)
    v, model, _ = make(2, audio=hiss)  # a silent hold
    asyncio.run(v.start_listening())
    assert asyncio.run(v.stop_listening("cr_2")) == ""
    assert "Release dropped: silent" in caplog.text and not model.calls
    assert "create a Python program" not in caplog.text  # numbers only, never words

def test_room_silence_never_reaches_whisper():
    # The loudest room silence measured on the demo laptop's headset mic: RMS 0.00022.
    rng = np.random.default_rng(1)
    hiss = (rng.standard_normal(RATE * 2) * 0.00022).astype(np.float32)
    v, model, _ = make(2, audio=hiss)
    asyncio.run(v.start_listening())
    assert asyncio.run(v.stop_listening("cr_1")) == ""
    assert model.calls == []


def test_segments_whisper_doubts_are_dropped():
    # Real case: silence came back as "Thank you." with no_speech_prob 0.70.
    v, _, _ = make(2, segs=[Seg(" Thank you.", 0.70), Seg(" run main.py", 0.05)])
    asyncio.run(v.start_listening())
    assert asyncio.run(v.stop_listening("cr_1")) == "run main.py"


def test_quiet_word_inside_a_long_hold_still_reaches_whisper():
    # Avery's probe on #24: 1 s of quiet speech (RMS 0.003) inside a 10 s hold of room hiss.
    rng = np.random.default_rng(2)
    hold = (rng.standard_normal(RATE * 10) * 0.0002).astype(np.float32)
    hold[RATE * 4 : RATE * 5] += speech_like(1, level=0.003)
    v, model, _ = make(10, audio=hold, text="cancel")
    asyncio.run(v.start_listening())
    assert asyncio.run(v.stop_listening("cr_1")) == "cancel"
    assert len(model.calls) == 1


def test_short_commands_survive_the_segment_filter():
    # Measured on small.en: cancel, stop, repeat, yes, no all scored no_speech_prob <= 0.07.
    v, _, _ = make(1, segs=[Seg(" Cancel.", 0.07)])
    asyncio.run(v.start_listening())
    assert asyncio.run(v.stop_listening("cr_1")) == "Cancel."


def test_speech_stops_and_earcon_ends_before_mic_opens():
    log = []
    v, _, _ = make(1, log=log)
    asyncio.run(v.start_listening())
    asyncio.run(v.stop_listening("cr_1"))
    assert log == ["stop_speech", "listening", "mic_open", "stopped"]


def test_key_released_during_earcon_never_opens_mic():
    log = []
    v, model, streams = make(1, earcon_s=0.05, log=log)

    async def tap():
        start = asyncio.create_task(v.start_listening())
        await asyncio.sleep(0.01)  # released mid-beep
        text = await v.stop_listening("cr_1")
        await start
        return text

    assert asyncio.run(tap()) == ""
    assert streams == [] and not v.listening and "mic_open" not in log


def test_every_release_ends_with_the_stopped_earcon():
    # docs/demo.md row 2 promises rising, then falling. A tap shorter than the 140 ms listening
    # earcon never opens the mic, but the user still hears that it is off.
    log = []
    v, _, _ = make(1, earcon_s=0.05, log=log)

    async def tap():
        start = asyncio.create_task(v.start_listening())
        await asyncio.sleep(0.01)
        await v.stop_listening("cr_1")
        await start
        await v.stop_listening("cr_2")  # the client's follow-up stop for a late start: no second tone

    asyncio.run(tap())
    assert log == ["stop_speech", "listening", "stopped"]

    for audio in (speech_like(0.1), np.zeros(RATE, np.float32)):  # too short, and silence: both skip Whisper
        log = []
        v, model, _ = make(1, log=log, audio=audio)
        asyncio.run(v.start_listening())
        assert asyncio.run(v.stop_listening("cr_1")) == "" and model.calls == []
        assert log == ["stop_speech", "listening", "mic_open", "stopped"]


def test_failed_start_speaks_its_error_and_adds_no_stopped_earcon():
    log = []

    def broken(cb):
        raise OSError("Error querying device -1")

    def earcon(name):
        log.append(name)
        return 0.0

    v = WhisperVoice(announcer=FakeAnnouncer(log), model=FakeModel("x"), stream_factory=broken, earcon=earcon)
    with pytest.raises(VoiceError):
        asyncio.run(v.start_listening())
    assert asyncio.run(v.stop_listening("cr_1")) == ""
    assert log == ["stop_speech", "listening"]


def test_release_and_repress_during_earcon_opens_one_mic():
    log = []
    v, _, streams = make(1, earcon_s=0.14, log=log)

    async def bounce():
        first = asyncio.create_task(v.start_listening())
        await asyncio.sleep(0.05)
        await v.stop_listening("cr_1")  # key up mid-beep
        await asyncio.sleep(0.02)
        second = asyncio.create_task(v.start_listening())  # key down again
        await asyncio.gather(first, second)
        await v.stop_listening("cr_2")

    asyncio.run(bounce())
    assert len(streams) == 1 and streams[0].closed and not v.listening  # no leaked, still-open mic


def test_first_request_is_primed_with_wake_word_before_any_project():
    v, model, streams = make(2)
    assert v.prompt == LIST_PROMPT
    asyncio.run(v.start_listening())
    asyncio.run(v.stop_listening("cr_0"))
    assert model.calls[0][1]["initial_prompt"] == LIST_PROMPT


def test_transcribes_and_strips_wake_word():
    v, model, streams = make(2)
    v.set_vocabulary(["main.py", "LoginForm"])
    asyncio.run(v.start_listening())
    assert v.listening
    text = asyncio.run(v.stop_listening("cr_1"))
    assert text == "create a Python program"
    assert not v.listening and streams[0].closed
    n, kw = model.calls[0]
    assert n == 2 * RATE and kw["initial_prompt"] == LIST_PROMPT + ", LoginForm" and kw["language"] == "en"
    assert v._chunks == []  # audio dropped after transcription


def test_key_tap_and_stop_without_start_return_empty():
    v, model, _ = make(0.1)
    assert asyncio.run(v.stop_listening("cr_0")) == ""
    asyncio.run(v.start_listening())
    assert asyncio.run(v.stop_listening("cr_1")) == ""
    assert model.calls == []


def test_repeat_start_keeps_one_stream():
    v, _, streams = make(1)
    asyncio.run(v.start_listening())
    asyncio.run(v.start_listening())
    assert len(streams) == 1


def test_recording_is_capped():
    log = []
    v, model, _ = make(MAX_SECONDS + 5, log=log)
    asyncio.run(v.start_listening())
    asyncio.run(v.stop_listening("cr_1"))
    assert model.calls[0][0] == MAX_SECONDS * RATE
    assert log[-1] == "stopped"  # the cap drops audio silently; the release still sounds


@pytest.mark.parametrize("post_roll", [None, 0.01])  # the default, and the opt-in post-roll
def test_mic_unplugged_mid_recording_raises_voice_error(post_roll):
    log = []
    v, model, streams = make(1, log=log, post_roll=post_roll)
    asyncio.run(v.start_listening())

    def gone():
        raise OSError("Unanticipated host error")

    streams[0].stop = gone
    with pytest.raises(VoiceError, match="stopped unexpectedly"):
        asyncio.run(v.stop_listening("cr_1"))
    assert not v.listening and v._chunks == [] and model.calls == [] and v._tail is None
    assert log[-1] == "stopped"  # by default before the failing close; with a post-roll, after it


def test_missing_microphone_raises_voice_error():
    def broken(cb):
        raise OSError("Error querying device -1")

    v = WhisperVoice(announcer=FakeAnnouncer([]), model=FakeModel("x"), stream_factory=broken, earcon=lambda n: 0.0)
    for _ in range(2):  # a failed start doesn't leave the key "held"; retrying raises again
        with pytest.raises(VoiceError, match="Microphone not available"):
            asyncio.run(v.start_listening())
    assert not v.listening


# ----- working cue (ARTEMIS_WORKING_CUE=earcon; the core's "Still working." heartbeat) -----


class Speaking:
    name = "sapi"

    def __init__(self, busy=False):
        self.is_busy, self.said = busy, []

    def available(self):
        return True

    def speak(self, text, interrupt):
        self.said.append(text)

    def cancel(self):
        pass

    def busy(self):
        return self.is_busy


def working_voice(on=True, busy=False, sapi_up=True):
    from artemis.voice.speech import Announcer

    sapi = Speaking(busy)
    sapi.available = lambda: sapi_up
    played = []
    v = WhisperVoice(announcer=Announcer(nvda=Speaking(), sapi=sapi, is_nvda_running=lambda: False),
                     model=FakeModel("x"), stream_factory=lambda cb: FakeStream(cb, speech_like(1)),
                     earcon=lambda name: (played.append(name), 0.0)[1], working_earcon=on)
    return v, played, sapi


def test_working_cue_is_off_by_default(monkeypatch):
    monkeypatch.delenv("ARTEMIS_WORKING_CUE", raising=False)
    v, _, _ = working_voice(on=None)
    assert v.working_cue is None  # the core keeps saying "Still working."
    monkeypatch.setenv("ARTEMIS_WORKING_CUE", "earcon")
    v, _, _ = working_voice(on=None)
    assert callable(v.working_cue)


def test_working_cue_ticks_but_never_over_speech_or_into_the_mic():
    v, played, sapi = working_voice()
    v.working_cue()
    assert played == ["working"]
    sapi.is_busy = True
    v.working_cue()  # Artemis is talking: skip this beat
    sapi.is_busy = False
    asyncio.run(v.start_listening())
    v.working_cue()  # the mic is open: a tick would be transcribed
    asyncio.run(v.stop_listening("cr_1"))
    assert played == ["working", "listening", "stopped"]  # one tick; only push-to-talk's own cues after it


def test_no_working_tick_when_no_voice_can_speak():
    v, _, _ = working_voice(sapi_up=False)
    assert v.working_cue is None  # voice_ready is false: the live region needs the words


def test_core_heartbeat_plays_the_tick_instead_of_the_line(tmp_path):
    from artemis.core.session import STILL_WORKING, Session

    for on in (False, True):
        v, played, sapi = working_voice(on=on)
        session = Session("ses_t", voice=v, runner=None, agent=None, projects_dir=tmp_path)
        asyncio.run(session._still_working("tsk_1"))
        if on:
            assert played == ["working"] and STILL_WORKING not in sapi.said
        else:
            assert played == [] and sapi.said == [STILL_WORKING]


def test_transcribing_is_true_only_while_whisper_runs():
    # voice.activity reads this: "transcribing" between the mic closing and the text being ready.
    v, _, _ = make(1)
    seen = []
    real = v._transcribe

    def slow(audio):
        seen.append(v.transcribing)
        return real(audio)

    v._transcribe = slow

    async def run():
        assert not v.transcribing
        await v.start_listening()
        await v.stop_listening("cr_1")

    asyncio.run(run())
    assert seen == [True] and not v.transcribing


def test_warm_initialises_audio_so_the_first_press_is_fast(monkeypatch):
    # Measured on Ubuntu: import sounddevice takes 1.5 s (PortAudio probes the devices). warm() runs at
    # core startup, so after it the first push-to-talk doesn't pay that cost.
    import sys

    pytest.importorskip("sounddevice")
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    v, _, _ = make(1)
    v.warm()
    assert "sounddevice" in sys.modules


class SpeakingAnnouncer(FakeAnnouncer):
    async def announce(self, text, priority):
        self.log.append(("say", text, priority))


def test_low_lines_are_not_spoken_while_push_to_talk_has_the_mic():
    # "Still working." during a long run must not be recorded into "cancel" (a whole-utterance control word).
    log = []
    v, _, _ = make(1, log=log)
    v.announcer = SpeakingAnnouncer(log)

    async def run():
        await v.announce("Still working.", "low")  # idle: spoken
        v._wanted = True  # pressed: the listening earcon and the mic are next
        await v.announce("Still working.", "low")  # not spoken
        await v.announce("Created main.py, 1 line.", "normal")  # normal lines are unchanged
        v._wanted = False
        v.transcribing = True
        await v.announce("Still working.", "low")  # not spoken while Whisper runs
        v.transcribing = False
        await v.announce("Still working.", "low")  # spoken again

    asyncio.run(run())
    said = [e for e in log if isinstance(e, tuple)]
    assert said == [("say", "Still working.", "low"), ("say", "Created main.py, 1 line.", "normal"), ("say", "Still working.", "low")]
