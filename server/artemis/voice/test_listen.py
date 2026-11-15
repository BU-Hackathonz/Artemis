"""WhisperVoice tests with a fake microphone and a fake model. Needs the voice group (numpy)."""

import asyncio

import pytest

np = pytest.importorskip("numpy")  # skip, don't fail collection, without the voice group

from artemis.voice.listen import MAX_SECONDS, RATE, VoiceError, WhisperVoice  # noqa: E402
from artemis.voice.text import CORRECTIONS, HOTWORDS, NATURAL_LEAD, correct_transcript

# main's #98 prompt plus the demo's coding words, still a word list (the default)
LIST_PROMPT = ("Hey Artemis, Artemis, Python, main.py, print, prints, Hello World, function, variable, program, run, "
               "file, create")
DEMO_WORDS = ["Python", "print", "prints", "Hello World", "main.py", "function", "variable", "program", "run", "file",
              "create"]
SETTINGS = ("ARTEMIS_WHISPER_MODEL", "ARTEMIS_WHISPER_BEAM", "ARTEMIS_PTT_POST_ROLL", "ARTEMIS_WHISPER_PROMPT",
            "ARTEMIS_WHISPER_HOTWORDS", "ARTEMIS_WORKING_CUE")


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
    assert "hotwords" not in kw  # opt-in
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


def test_word_list_prompt_and_hotwords_carry_the_demo_vocabulary():
    words = [w.strip() for w in LIST_PROMPT.split(",")]
    assert all(w in words for w in DEMO_WORDS)
    assert [w.strip() for w in HOTWORDS.split(",")] == words  # the same fixed list, without project words


def test_hotwords_are_opt_in_and_only_for_a_model_that_takes_them(monkeypatch):
    for raw in ("", "off", "1", "yes"):
        monkeypatch.setenv("ARTEMIS_WHISPER_HOTWORDS", raw)
        assert make(1)[0].hotwords is False, raw
    monkeypatch.setenv("ARTEMIS_WHISPER_HOTWORDS", "on")
    v, model, _ = make(1)
    v.set_vocabulary(["LoginForm"])
    asyncio.run(v.start_listening())
    asyncio.run(v.stop_listening("cr_1"))
    kw = model.calls[0][1]
    assert kw["hotwords"] == HOTWORDS and "LoginForm" not in kw["hotwords"]
    assert kw["initial_prompt"] == LIST_PROMPT + ", LoginForm"  # the prompt is unchanged

    class OldModel:  # faster-whisper before 1.0: no hotwords parameter
        def __init__(self):
            self.calls = []

        def transcribe(self, audio, language=None, beam_size=5, vad_filter=False, initial_prompt=None):
            self.calls.append((language, beam_size, vad_filter, initial_prompt))
            return iter([Seg(" create a Python program")]), None

    v, _, _ = make(1)
    v._model = old = OldModel()
    asyncio.run(v.start_listening())
    assert asyncio.run(v.stop_listening("cr_2")) == "create a Python program"  # no TypeError: the old call
    assert old.calls == [("en", 1, True, LIST_PROMPT)]


def test_the_locked_faster_whisper_takes_hotwords():
    fw = pytest.importorskip("faster_whisper")
    from artemis.voice.listen import takes_hotwords

    assert takes_hotwords(fw.WhisperModel.__new__(fw.WhisperModel))


@pytest.mark.parametrize("heard, meant", [
    ("create a Python program that princehello world", "create a Python program that prints hello world"),
    ("create a Python program that prince hello world", "create a Python program that prints hello world"),
    ("create a Python program that Prince, hello world.", "create a Python program that Prints hello world."),  # case kept
    ("Create a python program that princehelloworld", "Create a python program that prints hello world"),
    ("create a Python program that brings the low world", "create a Python program that prints hello world"),
    ("create a Python program that prints hollow world", "create a Python program that prints hello world"),
    ("prints Hallo World", "prints Hello world"),
    ("prints hullo-world", "prints hello world"),
    ("Hollow world.", "Hello world."),
    ("create a pie thon program", "create a python program"),
    ("create a Pi-thon program", "create a Python program"),
])
def test_known_mishearings_are_fixed(heard, meant):
    assert correct_transcript(heard) == meant


def test_every_correction_row_applies():
    for heard, meant in CORRECTIONS:
        assert correct_transcript(f"say {heard} now") == f"say {meant} now", heard
        assert correct_transcript(heard.upper()) == meant[:1].upper() + meant[1:], heard


@pytest.mark.parametrize("text", [
    "create a Python program that prints Hello World",
    "run main.py",
    "the prince said hello",  # "prince" alone, not before "hello"
    "a hollow log in the world",
    "pie chart for the thon",
    "",
])
def test_unrelated_text_is_unchanged(text, caplog):
    with caplog.at_level("INFO"):
        assert correct_transcript(text) == text
    assert "mishearing" not in caplog.text


@pytest.mark.parametrize("text", [
    "princehellos", "xprincehello", "prince helloworld", "prince hellos", "hollow worlds", "shallow world",
    "hallowed world", "pie thong", "pi thonic", "brings the low worldly", "apie thon", "princehello_x",
])
def test_corrections_never_apply_inside_other_words(text):
    assert correct_transcript(text) == text


def test_transcripts_are_corrected_and_only_the_count_is_logged(caplog):
    v, _, _ = make(1, text="Artemis,| create a Python program that princehello world")
    asyncio.run(v.start_listening())
    with caplog.at_level("INFO"):
        assert asyncio.run(v.stop_listening("cr_1")) == "create a Python program that prints hello world"
    assert "fixed 1 known mishearing" in caplog.text
    assert "prince" not in caplog.text.lower() and "hello" not in caplog.text.lower()


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


def working_voice(on=True, busy=False, sapi_up=True, durations=None, post_roll=None, model=None):
    from artemis.voice.speech import Announcer

    sapi = Speaking(busy)
    sapi.available = lambda: sapi_up
    played = []
    durations = durations or {}
    v = WhisperVoice(announcer=Announcer(nvda=Speaking(), sapi=sapi, is_nvda_running=lambda: False),
                     model=model or FakeModel("x"), stream_factory=lambda cb: FakeStream(cb, speech_like(1)),
                     earcon=lambda name: (played.append(name), durations.get(name, 0.0))[1], working_earcon=on,
                     post_roll=post_roll)
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


def heartbeat_events(session):
    return [e for e in session.bus.history if e.type == "agent.message" and e.data["kind"] == "progress"]


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


def test_the_log_still_gets_still_working_when_the_tick_replaces_it(tmp_path):
    # contracts.md 2: the client renders every event's announce.text in the log region. With the tick on,
    # a screen-reader user without sound must still find "Still working." there: same event, only unspoken.
    import json

    from artemis.core.session import STILL_WORKING, Session

    seen = {}
    for on in (False, True):
        v, _, _ = working_voice(on=on)
        session = Session(f"ses_{on}", voice=v, runner=None, agent=None, projects_dir=tmp_path, log_dir=tmp_path)
        queue = session.bus.subscribe()
        asyncio.run(session._still_working("tsk_1"))
        [event] = heartbeat_events(session)
        assert queue.get_nowait() is event  # the SSE stream gets it
        assert json.loads((tmp_path / f"ses_{on}.jsonl").read_text())["announce"]["text"] == STILL_WORKING
        seen[on] = event.model_dump(exclude={"seq", "session_id", "at"})
    assert seen[True] == seen[False]  # the client can't tell the two apart
    assert seen[True]["announce"] == {"text": STILL_WORKING, "priority": "low"}
    assert seen[True]["data"] == {"text": STILL_WORKING, "kind": "progress"}


def test_a_skipped_tick_still_sends_the_line_unspoken(tmp_path):
    from artemis.core.session import STILL_WORKING, Session

    v, played, sapi = working_voice()
    session = Session("ses_t", voice=v, runner=None, agent=None, projects_dir=tmp_path)

    async def run():
        await v.start_listening()  # push-to-talk owns the mic: no tick, and no spoken line either
        await session._still_working("tsk_1")
        await v.stop_listening("cr_1")

    asyncio.run(run())
    assert "working" not in played and sapi.said == []
    assert [e.announce.text for e in heartbeat_events(session)] == [STILL_WORKING]


def test_a_failing_tick_never_stops_the_log_line(tmp_path, caplog):
    from artemis.core.session import STILL_WORKING, Session

    v, _, sapi = working_voice()

    def broken(name):
        raise RuntimeError("no device")

    v._play_earcon = broken
    session = Session("ses_t", voice=v, runner=None, agent=None, projects_dir=tmp_path)
    asyncio.run(session._still_working("tsk_1"))
    assert [e.announce.text for e in heartbeat_events(session)] == [STILL_WORKING] and sapi.said == []
    assert "working cue failed" in caplog.text


def test_no_tick_from_the_press_until_the_mic_closes():
    # The listening earcon before the mic opens, the open mic, and the opt-in post-roll after release.
    v, played, _ = working_voice(post_roll=0.05)

    async def run():
        v._wanted = True  # pressed; the listening earcon is still playing
        assert not v._tick()
        v._wanted = False
        await v.start_listening()
        assert not v._tick()  # the mic is open
        stop = asyncio.create_task(v.stop_listening("cr_1"))
        await asyncio.sleep(0.01)
        assert v._tail is not None and not v._tick()  # post-roll: still recording
        await stop

    asyncio.run(run())
    assert played == ["listening", "stopped"]


def test_no_tick_while_whisper_transcribes():
    seen = []

    class Slow(FakeModel):
        def transcribe(self, audio, **kw):
            seen.append(v._tick())  # the heartbeat lands while Whisper runs
            return super().transcribe(audio, **kw)

    v, played, _ = working_voice(model=Slow("hello"))

    async def run():
        await v.start_listening()
        await v.stop_listening("cr_1")

    asyncio.run(run())
    assert seen == [False] and "working" not in played
    assert v._tick() and played[-1] == "working"  # after it, the next beat ticks


def test_no_tick_while_another_earcon_is_still_playing():
    import time

    v, played, _ = working_voice(durations={"stopped": 0.05, "working": 0.09})

    async def run():
        await v.start_listening()
        await v.stop_listening("cr_1")

    asyncio.run(run())
    assert not v._tick()  # "stopped" is still audible: a tick would cut it off (sounddevice plays one at a time)
    time.sleep(0.06)
    assert v._tick() and played[-1] == "working"


def test_no_tick_over_a_line_the_fallback_is_saying():
    # ElevenLabs handed the line to the local voice: current() isn't busy, but Artemis is audible.
    v, played, _ = working_voice()
    v.announcer.speaking_engine = lambda: "speechd"
    assert not v._tick()
    v.announcer.speaking_engine = lambda: None
    assert v._tick()

    def broken():
        raise OSError("gone")

    v.announcer.speaking_engine = broken
    assert not v._tick()  # can't tell: stay quiet
    assert played == ["working"]


def test_no_tick_while_the_wake_word_request_is_being_heard():
    class Wake:
        armed = True

    v, played, _ = working_voice()
    v.wake_listener = Wake()
    assert not v._tick()
    Wake.armed = False
    assert v._tick() and played == ["working"]


def test_the_wake_listener_drops_the_mic_during_the_tick():
    import time

    from artemis.voice.listen import TICK_GUARD_S
    from artemis.voice.wake import WakeListener

    v, _, _ = working_voice(durations={"working": 0.09})
    listener = WakeListener(v, lambda text: None, stream_factory=lambda cb: FakeStream(cb, speech_like(0)))
    assert not listener._muted()
    assert v._tick() and listener._muted()  # the tick is not a request
    time.sleep(0.09 + TICK_GUARD_S + 0.02)
    assert not listener._muted()


def test_the_tick_is_an_earcon_on_the_shared_path_short_quiet_and_precomputed():
    import inspect

    from artemis.voice import earcons

    default = inspect.signature(WhisperVoice).parameters["earcon"].default
    assert default is earcons.play  # the same player as "listening" and "stopped"
    sound = earcons.SOUNDS["working"]
    assert sound.dtype == np.float32 and len(sound) * 1000 // earcons.RATE == 90 <= earcons.MAX_MS
    assert float(np.abs(sound).max()) <= earcons.VOLUME / 2 + 1e-6  # half the other earcons
    v, played, _ = working_voice()
    v.working_cue()
    assert played == ["working"]  # the voice's own earcon player, by name: no new audio path


def test_a_tick_costs_almost_nothing():
    import time

    v, played, _ = working_voice()
    began = time.perf_counter()
    for _ in range(1000):
        v.working_cue()
    assert time.perf_counter() - began < 1.0  # one beat every 3 s; a thousand take well under a second
    assert played.count("working") == 1000


def test_app_start_wake_tells_the_voice_about_the_listener(tmp_path, monkeypatch):
    from artemis.core.test_settings import make_core
    from artemis.voice import wake

    class Listener:
        armed = False

        def __init__(self, voice, on_request):
            pass

        def start(self):
            pass

    monkeypatch.setattr(wake, "WakeListener", Listener)
    v, _, _ = working_voice()
    core = make_core(v, tmp_path)
    core.start_wake()
    assert isinstance(v.wake_listener, Listener) and core.wake is v.wake_listener


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

    try:
        import sounddevice  # noqa: F401
    except (ImportError, OSError):  # no PortAudio on this machine (a CI container): nothing to initialise
        pytest.skip("sounddevice or PortAudio is not installed")
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    v, _, _ = make(1)
    v.warm()
    assert "sounddevice" in sys.modules


# ----- why a release gave no request (contracts.md 5.1, Proposed (Avery)) -----


def hiss(seconds, level=0.0002):
    return (np.random.default_rng(3).standard_normal(int(RATE * seconds)) * level).astype(np.float32)


def release(v):
    async def run():
        await v.start_listening()
        return await v.stop_listening("cr_1")

    return asyncio.run(run())


def test_a_silent_hold_says_it_was_silent_and_never_reaches_whisper():
    v, model, _ = make(2, audio=hiss(2))
    heard = release(v)
    assert heard == "" and heard.dropped == "silent" and model.calls == []


def test_a_tap_is_short_and_a_repeated_stop_has_no_reason():
    v, model, _ = make(0.1)
    heard = release(v)
    assert heard == "" and heard.dropped == "short" and model.calls == []
    again = asyncio.run(v.stop_listening("cr_2"))  # the client's stop barrier: not a release
    assert again == "" and getattr(again, "dropped", None) is None


def test_let_go_during_the_listening_earcon_is_short():
    v, _, streams = make(1, earcon_s=0.05)

    async def tap():
        start = asyncio.create_task(v.start_listening())
        await asyncio.sleep(0.01)
        heard = await v.stop_listening("cr_1")
        await start
        return heard

    heard = asyncio.run(tap())
    assert heard == "" and heard.dropped == "short" and streams == []


def test_speech_is_a_normal_transcript_and_sound_without_words_is_empty():
    v, _, _ = make(2, text="Artemis,| run main.py")
    heard = release(v)
    assert heard == "run main.py" and getattr(heard, "dropped", None) is None
    v, model, _ = make(2, segs=[Seg(" Thank you.", 0.70)])  # loud enough, but Whisper's words are dropped
    heard = release(v)
    assert heard == "" and heard.dropped == "empty" and len(model.calls) == 1
    v, _, _ = make(2, text="Hey Artemis.")  # only the wake word
    assert release(v).dropped == "empty"


def core_with(v, tmp_path):
    from artemis.core.test_activity import FakeSpeaker
    from artemis.core.test_settings import make_core
    from artemis.voice.speech import Announcer

    v.announcer = Announcer(nvda=FakeSpeaker("nvda"), sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: False)
    return make_core(v, tmp_path)


async def hold_and_release(client, auth, stops=1):
    await client.post("/voice/listen", headers=auth, json={"action": "start"})
    for _ in range(stops):
        await client.post("/voice/listen", headers=auth, json={"action": "stop"})
    await asyncio.sleep(0.2)


@pytest.mark.parametrize("audio, text, line", [
    (hiss(2), "", True),  # a silent hold: the one fixed line
    (speech_like(0.1), "", False),  # a tap
    (None, "create a Python program", False),  # speech
])
async def test_only_a_silent_hold_through_the_core_says_check_the_microphone(tmp_path, monkeypatch, audio, text, line):
    from artemis.core.test_settings import TOKEN, serve

    monkeypatch.delenv("ARTEMIS_CONVERSATION", raising=False)
    monkeypatch.delenv("ARTEMIS_LISTEN_MODE", raising=False)
    v, _, _ = make(2, audio=audio)
    core = core_with(v, tmp_path)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    async with serve(core) as client:
        sid = (await client.post("/sessions", headers=auth)).json()["session_id"]
        await hold_and_release(client, auth, stops=2)  # a release, then the client's stop barrier
        history = core.sessions[sid].bus.history
        errors = [e for e in history if e.type == "error"]
        finals = [e for e in history if e.type == "transcript.final"]
        # The stop barrier's empty transcript can come first while Whisper runs; two empties count once.
        assert [f.data["text"] for f in finals if f.data["text"]] == ([text] if text else [])
        assert len(finals) == (2 if text else 1)
        if line:
            assert len(errors) == 1 and errors[0].task_id is None
            assert errors[0].data == {"code": "no_speech", "message": "I didn't hear anything.", "recovery": "Check the microphone."}
            assert errors[0].announce.model_dump() == {"text": "I didn't hear anything. Check the microphone.", "priority": "normal"}
        else:
            assert errors == []


async def test_wake_mode_room_silence_says_nothing(tmp_path, monkeypatch):
    from artemis.core.test_settings import TOKEN, serve
    from artemis.voice.wake import FRAME, WakeListener

    monkeypatch.delenv("ARTEMIS_CONVERSATION", raising=False)
    v, model, _ = make(2)
    core = core_with(v, tmp_path)

    class Mic:
        def __init__(self, callback):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    auth = {"Authorization": f"Bearer {TOKEN}"}
    async with serve(core) as client:
        sid = (await client.post("/sessions", headers=auth)).json()["session_id"]
        listener = WakeListener(v, core.wake_request, stream_factory=Mic)
        listener.start()
        try:
            room = hiss(5)
            for i in range(0, len(room), FRAME):  # five seconds of room silence, hands-free
                listener._on_audio(room[i : i + FRAME].reshape(-1, 1), FRAME, None, None)
            await asyncio.sleep(0.5)
        finally:
            listener.stop()
        history = core.sessions[sid].bus.history
        assert model.calls == []
        assert not [e for e in history if e.type in ("error", "transcript.final")]


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
