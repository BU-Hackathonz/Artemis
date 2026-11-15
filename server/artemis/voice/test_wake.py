"""Offline tests for the wake word: synthetic audio and scripted transcripts, no microphone."""

import asyncio

import pytest

np = pytest.importorskip("numpy")

from artemis.voice.wake import FRAME, RATE, Segmenter, WakeListener, WakeLoop, listen_mode, wake_request  # noqa: E402

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
