"""WhisperVoice tests with a fake microphone and a fake model. Needs the voice group (numpy)."""

import asyncio

import pytest

np = pytest.importorskip("numpy")  # skip, don't fail collection, without the voice group

from artemis.voice.listen import MAX_SECONDS, RATE, VoiceError, WhisperVoice  # noqa: E402


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


def make(seconds, text="Artemis,| create a Python program", earcon_s=0.0, log=None, audio=None, segs=None):
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
    v = WhisperVoice(announcer=FakeAnnouncer(log), model=model, stream_factory=factory, earcon=earcon)
    return v, model, streams


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
    assert v.prompt == "Artemis, Python, main.py"
    asyncio.run(v.start_listening())
    asyncio.run(v.stop_listening("cr_0"))
    assert model.calls[0][1]["initial_prompt"] == "Artemis, Python, main.py"


def test_transcribes_and_strips_wake_word():
    v, model, streams = make(2)
    v.set_vocabulary(["main.py", "LoginForm"])
    asyncio.run(v.start_listening())
    assert v.listening
    text = asyncio.run(v.stop_listening("cr_1"))
    assert text == "create a Python program"
    assert not v.listening and streams[0].closed
    n, kw = model.calls[0]
    assert n == 2 * RATE and kw["initial_prompt"] == "Artemis, Python, main.py, LoginForm" and kw["language"] == "en"
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
    v, model, _ = make(MAX_SECONDS + 5)
    asyncio.run(v.start_listening())
    asyncio.run(v.stop_listening("cr_1"))
    assert model.calls[0][0] == MAX_SECONDS * RATE


def test_mic_unplugged_mid_recording_raises_voice_error():
    v, model, streams = make(1)
    asyncio.run(v.start_listening())

    def gone():
        raise OSError("Unanticipated host error")

    streams[0].stop = gone
    with pytest.raises(VoiceError, match="stopped unexpectedly"):
        asyncio.run(v.stop_listening("cr_1"))
    assert not v.listening and v._chunks == [] and model.calls == []


def test_missing_microphone_raises_voice_error():
    def broken(cb):
        raise OSError("Error querying device -1")

    v = WhisperVoice(announcer=FakeAnnouncer([]), model=FakeModel("x"), stream_factory=broken, earcon=lambda n: 0.0)
    for _ in range(2):  # a failed start doesn't leave the key "held"; retrying raises again
        with pytest.raises(VoiceError, match="Microphone not available"):
            asyncio.run(v.start_listening())
    assert not v.listening
