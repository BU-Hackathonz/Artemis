"""Offline tests for the wake word: synthetic audio and scripted transcripts, no microphone."""

import asyncio

import pytest

np = pytest.importorskip("numpy")

from artemis.voice.wake import (  # noqa: E402
    FRAME, RATE, Segmenter, VoiceDetector, WakeListener, WakeLoop, denoise, listen_mode, wake_request,
)

RNG = np.random.default_rng(7)


def quiet(seconds, level=0.0002):
    return [RNG.normal(0, level, FRAME).astype(np.float32) for _ in range(round(seconds * 10))]


def speech(seconds, level=0.08):
    t = np.arange(FRAME) / RATE
    return [(np.sin(2 * np.pi * 220 * t) * level * np.sqrt(2)).astype(np.float32) for _ in range(round(seconds * 10))]


def run(segmenter, frames):
    return [audio for frame in frames if (audio := segmenter.feed(frame)) is not None]


# ----- the phrase -----


@pytest.mark.parametrize(
    "said, rest",
    [
        ("Hey Artemis, check my codebase for security vulnerabilities.", "check my codebase for security vulnerabilities."),
        ("Hey, Artemis. Build a to-do app", "Build a to-do app"),
        ("hey artemus run main.py", "run main.py"),
        ("Hay Artimis, don't delete main.py", "don't delete main.py"),
        ("Okay Artemis what are you doing", "what are you doing"),
        ("Hey Artemis.", ""),
        (" Hi Artemis", ""),
    ],
)
def test_the_wake_phrase_is_recognized_and_the_rest_is_untouched(said, rest):
    assert wake_request(said) == rest


@pytest.mark.parametrize(
    "said",
    [
        "Artemis is the project we're demoing today.",  # talking about it, not to it
        "Artemis, build a to-do app",  # no greeting word: push-to-talk covers this
        "So I said hey Artemis and nothing happened",  # not at the start
        "Hey, are these the judges?",
        "Hey Arthur, check this out",
        "Hey artist, nice work",
        "",
        "Thank you.",
    ],
)
def test_other_speech_does_not_wake_it(said):
    assert wake_request(said) is None


# ----- cutting utterances out of the stream -----


def test_one_utterance_is_cut_out_with_its_first_sound():
    found = run(Segmenter(), quiet(1) + speech(1.5) + quiet(1.5))
    assert len(found) == 1
    seconds = len(found[0]) / RATE
    # 0.3 s kept from before the start, the speech, and the 0.8 s of quiet that ended it.
    assert 2.5 <= seconds <= 2.7


def test_silence_and_short_clicks_are_not_utterances():
    assert run(Segmenter(), quiet(5)) == []
    assert run(Segmenter(), quiet(1) + speech(0.2) + quiet(2)) == []


def test_a_short_pause_does_not_end_the_sentence():
    found = run(Segmenter(), quiet(1) + speech(1) + quiet(0.5) + speech(1) + quiet(1.5))
    assert len(found) == 1 and len(found[0]) / RATE > 3.0


def test_two_sentences_with_a_long_gap_are_two_utterances():
    assert len(run(Segmenter(), quiet(1) + speech(1) + quiet(1.5) + speech(1) + quiet(1.5))) == 2


def test_steady_background_noise_raises_the_bar_instead_of_holding_the_mic_open():
    segmenter = Segmenter()
    assert run(segmenter, quiet(5, level=0.003)) == []  # a loud room, but nobody near the mic
    assert segmenter.threshold > 0.008
    assert len(run(segmenter, speech(1.5) + quiet(1.5, level=0.003))) == 1



def test_normal_speech_on_a_quiet_headset_mic_is_heard():
    # Measured on Yaovi's EarPods mic: room 0.0001-0.0008, normal speech 0.0017-0.0046.
    # Under the old 0.004 bar this was missed; once the room is known, 0.0025 speech is an utterance.
    found = run(Segmenter(), quiet(1, level=0.0004) + speech(1.5, level=0.0025) + quiet(1.5, level=0.0004))
    assert len(found) == 1


def test_until_the_room_is_known_the_stricter_bar_applies():
    # The first frames could be a steady noisy room: they are learned as the floor, never heard as speech.
    segmenter = Segmenter()
    assert run(segmenter, speech(1.5, level=0.0025) + quiet(4, level=0.0004)) == []
    assert segmenter.threshold < 0.004  # a few seconds of real quiet later, the lower bar applies

def test_an_utterance_that_never_goes_quiet_is_cut_at_the_cap():
    segmenter = Segmenter(max_s=4.0)
    found = run(segmenter, quiet(1) + speech(6))
    assert len(found) == 1 and len(found[0]) / RATE == pytest.approx(4.0, abs=0.01)
    assert segmenter.capped is True


# ----- the loop -----


class Script:
    """Stands in for Whisper: returns the next scripted transcript for each utterance."""

    def __init__(self, *lines):
        self.lines = list(lines)
        self.heard = 0

    def __call__(self, audio):
        self.heard += 1
        return self.lines.pop(0)


def feed(loop, frames):
    return [event for frame in frames if (event := loop.feed(frame)) is not None]


def utterance(seconds=1.5):
    return speech(seconds) + quiet(1.2)


def test_a_request_in_one_breath():
    loop = WakeLoop(Script("Hey Artemis, check my codebase for security vulnerabilities."))
    events = feed(loop, quiet(1) + utterance())
    assert [(e.kind, e.text) for e in events] == [("request", "check my codebase for security vulnerabilities.")]
    assert (loop.stats.wakes, loop.stats.requests, loop.stats.ignored) == (1, 1, 0)


def test_wake_then_the_request_without_repeating_the_phrase():
    loop = WakeLoop(Script("Hey Artemis.", "Build a to-do app."))
    events = feed(loop, quiet(1) + utterance(0.8) + utterance())
    assert [(e.kind, e.text) for e in events] == [("wake", ""), ("request", "Build a to-do app.")]
    assert loop.armed is False  # one request per wake: the window closes again


def test_the_window_closes_when_nothing_follows_the_wake():
    loop = WakeLoop(Script("Hey Artemis.", "so anyway about lunch"), window_s=3.0)
    events = feed(loop, quiet(1) + utterance(0.8) + quiet(4) + utterance())
    assert [e.kind for e in events] == ["wake", "timeout", "ignored"]


def test_other_peoples_speech_is_ignored_and_its_words_are_not_kept():
    loop = WakeLoop(Script("I think the judges are coming over now", "Thank you."))
    events = feed(loop, quiet(1) + utterance() + utterance())
    assert [(e.kind, e.text) for e in events] == [("ignored", "8 words"), ("ignored", "2 words")]
    assert loop.stats.requests == 0 and "judges" not in repr(loop.stats)


def test_nothing_is_heard_while_artemis_is_speaking():
    talking = {"now": True}
    script = Script("Hey Artemis, run main.py")
    loop = WakeLoop(script, speaking=lambda: talking["now"])
    # Artemis's own voice says something that would wake it, through the speakers.
    assert feed(loop, quiet(0.5) + utterance()) == []
    assert script.heard == 0 and loop.stats.suppressed > 0
    # It stops; after the short tail, a real request is heard.
    talking["now"] = False
    events = feed(loop, quiet(1) + utterance())
    assert [(e.kind, e.text) for e in events] == [("request", "run main.py")]


def test_speech_cut_off_by_artemis_starting_to_talk_is_dropped():
    talking = {"now": False}
    script = Script("should never be asked")
    loop = WakeLoop(script, speaking=lambda: talking["now"])
    feed(loop, quiet(1) + speech(1))
    talking["now"] = True
    feed(loop, speech(1) + quiet(2))
    talking["now"] = False
    assert feed(loop, quiet(2)) == [] and script.heard == 0


def test_an_empty_transcript_is_nothing():
    loop = WakeLoop(Script(""))
    assert feed(loop, quiet(1) + utterance()) == []
    assert loop.stats.utterances == 1 and loop.stats.ignored == 0


def test_a_wake_phrase_inside_the_window_still_counts_as_a_wake():
    loop = WakeLoop(Script("Hey Artemis.", "Hey Artemis, run main.py"))
    events = feed(loop, quiet(1) + utterance(0.8) + utterance())
    assert [(e.kind, e.text) for e in events] == [("wake", ""), ("request", "run main.py")]
    assert loop.stats.wakes == 2


# ----- the live listener, with fakes -----


class FakeAnnouncer:
    def __init__(self):
        self.engine, self.busy = None, False

    def speaking_engine(self):
        return self.engine

    def current(self):
        outer = self

        class S:
            def busy(self):
                return outer.busy

        return S()


class FakeVoice:
    def __init__(self, transcripts):
        self.announcer = FakeAnnouncer()
        self.busy_listening = False
        self.transcribing = False
        self.transcripts = list(transcripts)
        self.earcons = []

    def _transcribe(self, audio):
        return self.transcripts.pop(0) if self.transcripts else ""

    def _earcon(self, name):
        self.earcons.append(name)
        return 0.0


class FakeStream:
    def __init__(self, callback):
        self.callback, self.started, self.closed = callback, False, False

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        self.closed = True


def test_listen_mode_is_opt_in(monkeypatch):
    monkeypatch.delenv("ARTEMIS_LISTEN_MODE", raising=False)
    assert listen_mode() == "push-to-talk"
    monkeypatch.setenv("ARTEMIS_LISTEN_MODE", "Wake")
    assert listen_mode() == "wake"


def test_muted_while_artemis_speaks_or_push_to_talk_owns_the_mic():
    voice = FakeVoice([])
    listener = WakeListener(voice, lambda text: None, stream_factory=FakeStream)
    assert not listener._muted()
    voice.announcer.engine = "elevenlabs"
    assert listener._muted()
    voice.announcer.engine, voice.announcer.busy = None, True  # a line is downloading: about to be heard
    assert listener._muted()
    voice.announcer.busy, voice.busy_listening = False, True
    assert listener._muted()
    voice.busy_listening, voice.transcribing = False, True
    assert listener._muted()


def test_a_request_reaches_the_core_on_the_event_loop_with_earcons():
    voice = FakeVoice(["Hey Artemis", "check my codebase"])
    got = []

    async def run():
        listener = WakeListener(voice, got.append, stream_factory=FakeStream)
        listener.start()
        try:
            for frame in [*quiet(1), *speech(0.6), *quiet(1), *speech(1.2), *quiet(1)]:
                listener._on_audio(frame.reshape(-1, 1), FRAME, None, None)
            for _ in range(200):
                if got:
                    break
                await asyncio.sleep(0.02)
        finally:
            listener.stop()

    asyncio.run(run())
    assert got == ["check my codebase"]
    assert voice.earcons == ["listening", "stopped"]


def test_other_peoples_speech_never_reaches_the_core(caplog):
    voice = FakeVoice(["so I told her about Artemis yesterday"])
    got = []

    async def run():
        listener = WakeListener(voice, got.append, stream_factory=FakeStream)
        listener.start()
        try:
            for frame in [*quiet(1), *speech(1.0), *quiet(1)]:
                listener._on_audio(frame.reshape(-1, 1), FRAME, None, None)
            await asyncio.sleep(0.5)
        finally:
            listener.stop()

    with caplog.at_level("INFO"):
        asyncio.run(run())
    assert got == [] and "told her" not in caplog.text and "7 words" in caplog.text


# ----- hearing the speaker, not the room -----


def noise(seconds, level=0.03):
    return [RNG.normal(0, level, FRAME).astype(np.float32) for _ in range(round(seconds * 10))]


def test_with_a_voice_detector_loud_noise_is_not_speech():
    # a fan or hiss as loud as speech: loudness would hold the mic open; the detector says "no voice"
    segmenter = Segmenter()
    frames = quiet(1) + noise(5)
    found = [a for f in frames if (a := segmenter.feed(f, voice=0.05)) is not None]
    assert found == [] and not segmenter.in_speech


def test_with_a_voice_detector_speech_ends_when_the_voice_does_even_in_noise():
    segmenter = Segmenter()
    frames = [(f, 0.02) for f in quiet(1)] + [(f, 0.9) for f in speech(1.5)] + [(f, 0.05) for f in noise(1.5)]
    found = [a for f, v in frames if (a := segmenter.feed(f, voice=v)) is not None]
    assert len(found) == 1 and len(found[0]) / RATE <= 2.65  # pre-roll + 1.5 s of speech + 0.8 s without a voice, not the noise


def test_staying_in_speech_takes_less_than_starting():
    segmenter = Segmenter()
    assert [segmenter.feed(f, voice=0.45) for f in speech(1)] == [None] * 10 and not segmenter.in_speech
    segmenter.feed(speech(0.1)[0], voice=0.9)
    for f in speech(1):
        segmenter.feed(f, voice=0.45)  # a softer stretch mid-sentence: still the same utterance
    assert segmenter.in_speech


def test_denoise_lowers_steady_noise_and_keeps_the_length():
    t = np.arange(RATE * 2) / RATE
    tone = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    hiss = np.random.default_rng(1).normal(0, 0.03, len(t)).astype(np.float32)
    room = np.random.default_rng(2).normal(0, 0.03, RATE).astype(np.float32)
    out = denoise(tone + hiss, room)
    assert len(out) == len(tone) and np.max(np.abs(out)) <= 0.9 + 1e-6
    spec = np.abs(np.fft.rfft(out))
    freqs = np.fft.rfftfreq(len(out), 1 / RATE)
    signal = spec[(freqs > 430) & (freqs < 450)].sum()
    rest = spec[(freqs > 1000)].sum()
    spec0 = np.abs(np.fft.rfft(tone + hiss))
    assert signal / rest > 1.8 * spec0[(freqs > 430) & (freqs < 450)].sum() / spec0[(freqs > 1000)].sum()


def test_denoise_removes_mains_hum():
    t = np.arange(RATE * 2) / RATE
    hum = (0.1 * np.sin(2 * np.pi * 60 * t)).astype(np.float32)
    voiceish = (0.1 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    out = denoise(hum + voiceish, np.zeros(RATE, np.float32) + 1e-4)
    spec, freqs = np.abs(np.fft.rfft(out)), np.fft.rfftfreq(len(out), 1 / RATE)
    assert spec[(freqs > 55) & (freqs < 65)].sum() < 0.05 * spec[(freqs > 295) & (freqs < 305)].sum()


@pytest.mark.parametrize("phantom", ["Thank you.", "you", " Thanks for watching!"])
def test_a_stock_phrase_from_noise_is_not_a_request(phantom):
    loop = WakeLoop(Script("Hey Artemis", phantom))
    events = feed(loop, quiet(1) + utterance(0.8) + utterance())
    assert [e.kind for e in events] == ["wake"]  # still armed; the phantom started nothing


def test_the_loop_endpoints_on_the_detector():
    voices = iter([0.02] * 10 + [0.9] * 15 + [0.05] * 40)
    loop = WakeLoop(Script("Hey Artemis, run main.py"), vad=lambda f: next(voices))
    events = feed(loop, quiet(1) + speech(1.5) + noise(4))  # loud noise after the request
    assert [(e.kind, e.text) for e in events] == [("request", "run main.py")]


def test_voice_detector_on_the_real_model():
    detector = VoiceDetector.load()
    if detector is None:
        pytest.skip("faster-whisper (voice group) is not installed")
    probs = [detector(f) for f in quiet(1) + noise(1)]
    assert max(probs) < 0.5  # hiss is not a voice


# ----- reacting fast: the early look at a pause -----


def test_a_bare_wake_phrase_arms_after_the_short_pause_not_the_full_end_silence():
    loop = WakeLoop(Script("Hey Artemis."))
    frames = quiet(1) + speech(0.8)
    assert feed(loop, frames) == []
    events = feed(loop, quiet(0.4))  # 0.4 s of quiet is enough for the wake phrase
    assert [(e.kind, e.quiet_s) for e in events] == [("wake", 0.4)]
    assert loop.armed


def test_without_the_early_look_the_wake_waits_for_the_full_end_silence():
    loop = WakeLoop(Script("Hey Artemis."), wake_silence_s=None)
    assert feed(loop, quiet(1) + speech(0.8) + quiet(0.4)) == []
    assert [e.kind for e in feed(loop, quiet(0.4))] == ["wake"]


def test_a_request_still_gets_the_full_end_silence_after_arming():
    loop = WakeLoop(Script("Hey Artemis.", "Build a to-do app."))
    feed(loop, quiet(1) + speech(0.8) + quiet(0.4))
    # a 0.5 s pause in the middle of the request does not end it
    events = feed(loop, speech(1) + quiet(0.5) + speech(1) + quiet(0.7))
    assert events == []
    assert [(e.kind, e.text) for e in feed(loop, quiet(0.2))] == [("request", "Build a to-do app.")]


def test_a_one_breath_request_is_transcribed_once_when_nothing_follows_the_pause():
    script = Script("Hey Artemis, run main.py")
    loop = WakeLoop(script)
    events = feed(loop, quiet(1) + utterance())
    assert [(e.kind, e.text) for e in events] == [("request", "run main.py")]
    assert script.heard == 1  # the early transcript was the final one


def test_speech_after_the_early_look_is_transcribed_again_in_full():
    script = Script("Hey Artemis, open the", "Hey Artemis, open the readme")
    loop = WakeLoop(script)
    events = feed(loop, quiet(1) + speech(1.5) + quiet(0.5) + speech(0.6) + quiet(1.2))
    assert [(e.kind, e.text) for e in events] == [("request", "open the readme")]
    assert script.heard == 2


def test_only_one_early_look_per_utterance():
    script = Script("I think the", "I think the judges are coming over now")
    loop = WakeLoop(script)
    frames = quiet(1) + speech(1) + quiet(0.5) + speech(1) + quiet(0.5) + speech(1) + quiet(1.2)
    assert [e.kind for e in feed(loop, frames)] == ["ignored"]
    assert script.heard == 2  # the first pause and the end, not every pause


def test_the_quick_model_arms_first_and_the_full_model_confirms():
    quick, full = Script("Hey Artemis."), Script("Hey Artemis!", "Build a to-do app.")
    loop = WakeLoop(full, quick=quick)
    events = feed(loop, quiet(1) + speech(0.8) + quiet(0.4))
    assert [e.kind for e in events] == ["wake"] and full.heard == 0  # armed on the quick model alone
    events = feed(loop, utterance())
    assert [(e.kind, e.text) for e in events] == [("request", "Build a to-do app.")]
    assert (quick.heard, full.heard, loop.stats.misheard) == (1, 2, 0)


def test_a_quick_wake_the_full_model_does_not_hear_is_taken_back():
    quick, full = Script("Hey Artemis.", "so anyway"), Script("Hey Arthur.", "so anyway about lunch")
    loop = WakeLoop(full, quick=quick)
    feed(loop, quiet(1) + speech(0.8) + quiet(0.4))
    events = feed(loop, quiet(0.1))
    assert [e.kind for e in events] == ["misheard"] and not loop.armed
    assert [e.kind for e in feed(loop, utterance())] == ["ignored"]  # never a request
    assert (loop.stats.wakes, loop.stats.requests, loop.stats.misheard) == (0, 0, 1)


def test_the_quick_model_is_not_asked_about_long_speech():
    quick, full = Script(), Script("Hey Artemis, check my codebase for security vulnerabilities.")
    loop = WakeLoop(full, quick=quick)
    events = feed(loop, quiet(1) + utterance(2.5))
    assert [e.kind for e in events] == ["request"] and quick.heard == 0


def test_speech_without_the_phrase_to_the_quick_model_waits_for_the_end():
    quick, full = Script("so anyway"), Script("so anyway about lunch")
    loop = WakeLoop(full, quick=quick)
    feed(loop, quiet(1) + speech(1) + quiet(0.4))
    assert full.heard == 0  # no full transcription at the pause
    assert [e.kind for e in feed(loop, quiet(0.8))] == ["ignored"] and full.heard == 1


def test_the_early_look_never_hears_artemis_speaking():
    talking = {"now": True}
    quick, full = Script(), Script()
    loop = WakeLoop(full, quick=quick, speaking=lambda: talking["now"])
    assert feed(loop, quiet(0.5) + speech(0.8) + quiet(1)) == []
    assert quick.heard == full.heard == 0


def test_a_misheard_wake_plays_the_stopped_earcon(monkeypatch):
    voice = FakeVoice([])
    listener = WakeListener(voice, lambda text: None, stream_factory=FakeStream)
    from artemis.voice.wake import Event
    listener._handle(Event("misheard"))
    assert voice.earcons == ["stopped"]


def test_quick_model_setting(monkeypatch):
    from artemis.voice.wake import quick_model
    monkeypatch.delenv("ARTEMIS_WAKE_QUICK_MODEL", raising=False)
    assert quick_model() == "tiny.en"
    monkeypatch.setenv("ARTEMIS_WAKE_QUICK_MODEL", "off")
    assert quick_model() is None
    monkeypatch.setenv("ARTEMIS_WAKE_QUICK_MODEL", "base.en")
    assert quick_model() == "base.en"


def test_a_quick_model_not_loaded_yet_leaves_the_early_look_to_the_full_model():
    full = Script("Hey Artemis.")
    loop = WakeLoop(full, quick=lambda audio: None)
    events = feed(loop, quiet(1) + speech(0.8) + quiet(0.4))
    assert [(e.kind, e.quiet_s) for e in events] == [("wake", 0.4)] and full.heard == 1
    assert feed(loop, quiet(0.1)) == []  # nothing to confirm: the full model already decided


def test_a_confirmation_that_fails_takes_the_quick_wake_back():
    def broken(audio):
        raise RuntimeError("whisper fell over")

    loop = WakeLoop(broken, quick=Script("Hey Artemis."))
    feed(loop, quiet(1) + speech(0.8) + quiet(0.4))
    assert [e.kind for e in feed(loop, quiet(0.1))] == ["misheard"] and not loop.armed
