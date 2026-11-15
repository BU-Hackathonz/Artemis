"""Voice tests. Run with pytest, or without it: python -m artemis.voice.test_voice (from server/)."""

import asyncio
import inspect
import logging

import pytest

from artemis.voice import Announcer, StubVoice, strip_wake_word, vocabulary_prompt
from artemis.voice.text import NATURAL_LEAD


class FakeSpeaker:
    def __init__(self, name="fake", up=True, busy=False):
        self.name, self.up, self.is_busy = name, up, busy
        self.said: list[tuple[str, bool]] = []
        self.canceled = 0

    def available(self):
        return self.up

    def speak(self, text, interrupt):
        self.said.append((text, interrupt))

    def cancel(self):
        self.canceled += 1

    def busy(self):
        return self.is_busy


def test_strip_wake_word():
    assert strip_wake_word("Artemis, create a file") == "create a file"
    assert strip_wake_word("hey artemis. run it") == "run it"
    assert strip_wake_word("Hey Artemus, create a Python program") == "create a Python program"  # "Hey Artemis" misheard
    assert strip_wake_word("  ARTEMIS run it") == "run it"
    assert strip_wake_word("run artemis.py") == "run artemis.py"  # only a leading wake word
    assert strip_wake_word("Artemisia is a plant") == "Artemisia is a plant"
    assert strip_wake_word("Artemis") == ""


def test_strip_wake_word_accepts_close_spellings():
    for variant in ["Artemus", "Artemiss", "Arthemis", "Ardemis", "Artimis", "ARTHEMIS"]:
        assert strip_wake_word(f"{variant}, create a file") == "create a file", variant
        assert strip_wake_word(f"hey {variant}. run it") == "run it", variant
        assert strip_wake_word(f"ok {variant} run it") == "run it", variant
    assert strip_wake_word("okay Artimis run it") == "run it"


def test_strip_wake_word_accepts_a_clipped_artem():
    # David's hardware run: "Hey Artemis, create a Python program..." came back as "Hey Artem, this is creative...".
    assert strip_wake_word("Hey Artem, this is creative Python program") == "this is creative Python program"
    for text in ["Artem, create a file", "artem. create a file", "ok Artem create a file", "Hey Ardem, create a file"]:
        assert strip_wake_word(text) == "create a file", text


def test_strip_wake_word_after_punctuated_greeting():
    # Whisper often writes a comma or period after the greeting: "Hey, Artemis," / "Okay. Artemis".
    for text in ["Hey, Artemis, run it.", "Hey, Artem, run it.", "Okay. Artemis run it.", "OK, Artemis. run it."]:
        assert strip_wake_word(text) == "run it.", text  # trailing punctuation is kept, as for "Cancel."
    for text in ["Hey, art is fun", "Okay, run it", "Temis, run it"]:
        assert strip_wake_word(text) == text, text


def test_strip_wake_word_leaves_a_possessive_or_file_name_alone():
    # The optional -is ending made "Artem's code is broken" come back as "'s code is broken".
    for text in ["Artem's code is broken", "Artemis's settings are wrong", "artemis.py is broken"]:
        assert strip_wake_word(text) == text, text


def test_strip_wake_word_leaves_other_words_alone():
    for text in ["artistic rendering", "art is hard", "If so, then create a program", "Artemisia is a plant",
                 "run artemus.py", "Armis is a name", "Artemia live in salt lakes", "run Artem"]:
        assert strip_wake_word(text) == text, text


LIST_LEAD = ("Hey Artemis, Artemis, Python, main.py, print, prints, Hello World, function, variable, program, run, "
             "file, create")  # the #98 word list plus the demo's coding words


def test_vocabulary_prompt_dedupes_and_caps():
    # The default: the word list.
    assert vocabulary_prompt(["main.py", "Main.py", " ", "LoginForm"]) == LIST_LEAD + ", LoginForm"
    assert vocabulary_prompt(["artemis", "PYTHON", "Print"]) == LIST_LEAD
    assert vocabulary_prompt([]) == LIST_LEAD
    cap = len(LIST_LEAD) + 10
    p = vocabulary_prompt([f"word{i}" for i in range(500)], max_chars=cap)
    assert len(p) <= cap and p.startswith(LIST_LEAD + ", word0")
    assert len(vocabulary_prompt([f"word{i}" for i in range(500)], max_chars=50)) <= 50


def test_natural_vocabulary_prompt_reads_like_speech_then_project_words():
    # Opt-in (ARTEMIS_WHISPER_PROMPT=natural).
    assert NATURAL_LEAD == "Hey Artemis, create a Python program, print, run it, main.py."
    assert "Hello World" not in NATURAL_LEAD  # primes words, never a whole demo sentence to copy
    assert vocabulary_prompt([], natural=True) == NATURAL_LEAD
    assert vocabulary_prompt(["main.py", "Main.py", " ", "LoginForm", "utils"], natural=True) == NATURAL_LEAD + " LoginForm, utils."
    assert vocabulary_prompt(["artemis", "PYTHON", "Run"], natural=True) == NATURAL_LEAD  # already in the lead


def test_natural_vocabulary_prompt_caps_its_length():
    for cap in (len(NATURAL_LEAD) + 7, len(NATURAL_LEAD) + 50, 600):
        p = vocabulary_prompt([f"word{i}" for i in range(500)], max_chars=cap, natural=True)
        assert len(p) <= cap and p.startswith(NATURAL_LEAD + " word0") and p.endswith(".")


def router(nvda_up=True, sapi_up=True, running=False, busy=False):
    nvda, sapi = FakeSpeaker("nvda", up=nvda_up), FakeSpeaker("sapi", up=sapi_up, busy=busy)
    state = {"running": running}
    return Announcer(nvda, sapi, is_nvda_running=lambda: state["running"]), nvda, sapi, state


def test_never_both_voices_nvda_started_later():
    a, nvda, sapi, state = router()
    asyncio.run(a.announce("one", "normal"))
    state["running"] = True  # NVDA started after the core
    asyncio.run(a.announce("two", "normal"))
    assert sapi.said == [("one", False)] and nvda.said == [("two", False)]
    assert a.screen_reader == "nvda" and a.voice_ready


def test_nvda_running_without_dll_stays_silent_not_sapi():
    a, nvda, sapi, _ = router(nvda_up=False, running=True)
    asyncio.run(a.announce("result", "high"))
    assert sapi.said == [] and nvda.said == []
    assert not a.voice_ready  # client's live region speaks through NVDA instead


def test_priority_rules():
    a, _, s, _ = router(busy=True)
    asyncio.run(a.announce("progress", "low"))  # dropped while busy
    asyncio.run(a.announce("Run failed, exit code 1", "high"))
    asyncio.run(a.announce("   ", "high"))  # empty is ignored
    assert s.said == [("Run failed, exit code 1", True)]
    asyncio.run(a.repeat_last())
    asyncio.run(a.stop_speech())
    assert s.said[-1] == ("Run failed, exit code 1", True) and s.canceled == 1


def test_no_speaker_reports_not_ready():
    a, *_ = router(sapi_up=False)
    assert not a.voice_ready and a.screen_reader is None
    asyncio.run(a.announce("still logged", "normal"))  # falls back to NullSpeaker, no crash


def test_stub_voice_returns_mvp_request():
    a, _, s, _ = router()
    v = StubVoice(announcer=a)
    asyncio.run(v.start_listening())
    assert v.listening and s.canceled == 1  # push-to-talk cut off speech first
    text = asyncio.run(v.stop_listening("cr_1"))
    assert text == "create a Python program that prints Hello World" and not v.listening
    asyncio.run(v.announce(f"Heard: {text}", "normal"))
    assert s.said == [("Heard: create a Python program that prints Hello World", False)]
    v.set_vocabulary(["LoginForm"])
    assert v.prompt == LIST_LEAD + ", LoginForm"


def test_watch_reports_at_start_and_on_change_only():
    a, nvda, sapi, state = router()
    seen = []

    async def run():
        async def cb(ready, reader):  # async callbacks are awaited
            seen.append((ready, reader))

        task = asyncio.create_task(a.watch(cb, interval_s=0.01))
        await asyncio.sleep(0.05)
        state["running"] = True  # NVDA started while idle
        await asyncio.sleep(0.05)
        nvda.up = False  # NVDA still running, DLL gone
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(run())
    assert seen == [(True, None), (True, "nvda"), (False, None)]


def test_watch_survives_a_failing_callback():
    a, _, _, state = router()
    seen = []

    def cb(ready, reader):
        seen.append(reader)
        if len(seen) == 1:
            raise RuntimeError("emit failed")

    async def run():
        task = asyncio.create_task(a.watch(cb, interval_s=0.01))
        await asyncio.sleep(0.05)
        state["running"] = True
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(run())
    assert seen == [None, "nvda"]


class FakeSapi(FakeSpeaker):
    """A SAPI-like speaker with a rate, clamped like the real one."""

    def __init__(self, rate=1):
        super().__init__("sapi")
        self.rate = rate

    def set_rate(self, rate):
        from artemis.voice.speech import clamp_rate

        self.rate = clamp_rate(rate)
        return self.rate


def test_faster_and_slower_change_sapi_rate_and_report_it():
    from artemis.voice.speech import RateStatus

    sapi = FakeSapi(rate=1)
    nvda = FakeSpeaker("nvda")
    a = Announcer(nvda, sapi, is_nvda_running=lambda: False)
    assert asyncio.run(a.set_rate(1)) is RateStatus.FASTER and sapi.rate == 2
    assert asyncio.run(a.set_rate(-1)) is RateStatus.SLOWER and sapi.rate == 1
    assert sapi.said == [] and nvda.said == []  # the core says the line, not the voice


def test_rate_is_bounded_and_reports_the_limit():
    from artemis.voice.speech import RATE_MAX, RATE_MIN, RateStatus

    sapi = FakeSapi(rate=RATE_MAX)
    a = Announcer(FakeSpeaker("nvda"), sapi, is_nvda_running=lambda: False)
    assert asyncio.run(a.set_rate(1)) is RateStatus.AT_MAX and sapi.rate == RATE_MAX
    sapi.rate = RATE_MIN
    assert asyncio.run(a.set_rate(-1)) is RateStatus.AT_MIN and sapi.rate == RATE_MIN
    assert sapi.said == []


@pytest.mark.parametrize("start, delta, end, status", [
    (8, 1, 8, "at_max"),      # above our max: "faster" must not slow it down
    (8, -1, 6, "slower"),     # "slower" comes back into range
    (-6, -1, -6, "at_min"),   # below our min: "slower" must not speed it up
    (-6, 1, -3, "faster"),
])
def test_rate_from_outside_our_range_never_steps_the_wrong_way(start, delta, end, status):
    from artemis.voice.speech import RateStatus

    sapi = FakeSapi(rate=start)
    a = Announcer(FakeSpeaker("nvda"), sapi, is_nvda_running=lambda: False)
    assert asyncio.run(a.set_rate(delta)) is RateStatus(status)
    assert sapi.rate == end and sapi.said == []


def test_a_zero_rate_step_changes_nothing():
    from artemis.voice.speech import RateStatus

    sapi = FakeSapi(rate=2)
    a = Announcer(FakeSpeaker("nvda"), sapi, is_nvda_running=lambda: False)
    assert asyncio.run(a.set_rate(0)) is RateStatus.UNCHANGED and sapi.rate == 2 and sapi.said == []


def test_rate_never_changes_under_nvda():
    from artemis.voice.speech import RateStatus

    nvda, sapi = FakeSpeaker("nvda"), FakeSapi(rate=1)
    a = Announcer(nvda, sapi, is_nvda_running=lambda: True)
    assert asyncio.run(a.set_rate(1)) is RateStatus.NVDA
    assert sapi.rate == 1 and sapi.said == [] and nvda.said == []


def test_no_voice_output_reports_no_output_and_says_nothing():
    from artemis.voice.speech import RateStatus

    a = Announcer(FakeSpeaker("nvda", up=False), FakeSpeaker("sapi", up=False), is_nvda_running=lambda: False)
    assert asyncio.run(a.set_rate(1)) is RateStatus.NO_OUTPUT


def test_set_rate_never_speaks_directly(monkeypatch):
    # The core owns every rate line now: no branch of set_rate may call announce().
    from artemis.voice.speech import Announcer as A

    async def no_announce(self, text, priority):
        raise AssertionError(f"set_rate spoke directly: {text!r}")

    monkeypatch.setattr(A, "announce", no_announce)
    sapi = FakeSapi(rate=6)
    for nvda_up, delta in [(True, 1), (False, 1), (False, -1), (False, 0)]:
        a = Announcer(FakeSpeaker("nvda"), sapi, is_nvda_running=lambda up=nvda_up: up)
        asyncio.run(a.set_rate(delta))
    a = Announcer(FakeSpeaker("nvda", up=False), FakeSpeaker("sapi", up=False), is_nvda_running=lambda: False)
    asyncio.run(a.set_rate(1))


def test_env_rate_is_optional_and_safe(monkeypatch):
    from artemis.voice.speech import RATE_MAX, _env_rate

    monkeypatch.delenv("ARTEMIS_SAPI_RATE", raising=False)
    assert _env_rate() is None  # unset: keep the user's own Windows rate
    monkeypatch.setenv("ARTEMIS_SAPI_RATE", "99")
    assert _env_rate() == RATE_MAX
    monkeypatch.setenv("ARTEMIS_SAPI_RATE", "fast")
    assert _env_rate() is None


class FakeToken:
    """A SAPI voice token: only GetDescription() is used by choose_voice."""

    def __init__(self, description):
        self._description = description

    def GetDescription(self):
        return self._description


class FakeSpVoice:
    """Stands in for SAPI.SpVoice: GetVoices() lists the tokens; assigning .Voice records the choice."""

    def __init__(self, descriptions=(), raises=None):
        self.tokens = [FakeToken(d) for d in descriptions]
        self._raises = raises
        self.Voice = "windows-default"

    def GetVoices(self):
        if self._raises is not None:
            raise self._raises
        return self.tokens


def sapi_with(voice):
    from artemis.voice.speech import SapiSpeaker

    s = SapiSpeaker.__new__(SapiSpeaker)  # bypass __init__: no COM, no pywin32 in tests
    s._voice = voice
    return s


INSTALLED = ["Microsoft David Desktop", "Microsoft Zira Desktop"]


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_choose_voice_sets_the_matching_voice():
    fake = FakeSpVoice(INSTALLED)
    assert sapi_with(fake).choose_voice("Zira") is True
    assert fake.Voice is fake.tokens[1]


def test_choose_voice_matches_case_insensitive_substring():
    fake = FakeSpVoice(INSTALLED)
    assert sapi_with(fake).choose_voice("DAVID") is True
    assert fake.Voice is fake.tokens[0]


def test_choose_voice_takes_the_first_installed_match():
    fake = FakeSpVoice(INSTALLED)  # "Microsoft" is in both descriptions
    assert sapi_with(fake).choose_voice("microsoft") is True
    assert fake.Voice is fake.tokens[0]


def test_choose_voice_unknown_name_keeps_voice_and_warns(caplog):
    fake = FakeSpVoice(INSTALLED)
    with caplog.at_level(logging.WARNING, logger="artemis.voice.speech"):
        assert sapi_with(fake).choose_voice("Hazel") is False
    assert fake.Voice == "windows-default"
    warnings = _warnings(caplog)
    assert len(warnings) == 1 and "Hazel" in warnings[0].getMessage()


@pytest.mark.parametrize("name", [None, ""])
def test_choose_voice_empty_name_is_a_silent_no_op(name, caplog):
    fake = FakeSpVoice(INSTALLED)
    assert sapi_with(fake).choose_voice(name) is False
    assert fake.Voice == "windows-default"
    assert _warnings(caplog) == []


def test_choose_voice_com_error_does_not_crash_and_warns(caplog):
    fake = FakeSpVoice(raises=RuntimeError("COM error"))
    with caplog.at_level(logging.WARNING, logger="artemis.voice.speech"):
        assert sapi_with(fake).choose_voice("Zira") is False
    assert fake.Voice == "windows-default"
    assert len(_warnings(caplog)) == 1


def test_sapi_init_survives_a_failing_rate_set():
    # Avery's review: a COM error while setting the rate at start-up must not stop the core.
    import pytest

    pytest.importorskip("win32com.client")
    from artemis.voice.speech import SapiSpeaker

    class BrokenRate(SapiSpeaker):
        def set_rate(self, rate):
            raise RuntimeError("COM error")

    s = BrokenRate(rate=3)  # must not raise
    assert s.available()


def test_stub_voice_passes_set_rate_through():
    from artemis.voice.speech import RateStatus

    sapi = FakeSapi(rate=0)
    v = StubVoice(announcer=Announcer(FakeSpeaker("nvda"), sapi, is_nvda_running=lambda: False))
    assert asyncio.run(v.set_rate(1)) is RateStatus.FASTER and sapi.rate == 1



def test_speechlint_flags_run_on_sentences():
    from artemis.voice.speechlint import run_ons

    assert run_ons("Result It worked.") == ["Result It"]
    assert run_ons("No input device Check the microphone, or type the request.") == ["device Check"]
    assert run_ons("Give me a second, I'm busy. Retrying.") == []
    assert run_ons("Artemis is ready. Hold Control Shift Space and speak, or type a request.") == []
    assert run_ons("Echo Check found 2 problems.") == []  # a product name, not a run-on


def test_server_string_literals_have_no_run_ons():
    from pathlib import Path

    from artemis.voice.speechlint import scan

    assert scan(Path(__file__).resolve().parents[1]) == []


def test_mic_errors_end_a_sentence_before_the_recovery_tip():
    pytest.importorskip("numpy")  # listen.py needs numpy; CI's server job has no voice group
    from artemis.voice.listen import _sentence
    from artemis.voice.speechlint import run_ons

    recovery = "Check the microphone, or type the request."  # core/app.py appends this after a space
    for raw in ["No Default Input Device Available", "Error querying device -1.", ""]:
        message = _sentence("Microphone not available", RuntimeError(raw))
        assert message.endswith(".") and ".." not in message
        assert run_ons(f"{message} {recovery}") == []
    assert _sentence("Microphone not available", RuntimeError("")) == "Microphone not available."
def test_earcons_short_quiet_and_distinct():
    pytest.importorskip("numpy")  # CI's server job has no voice group
    import numpy as np

    from artemis.voice.earcons import MAX_MS, RATE, SOUNDS, VOLUME

    for name, sound in SOUNDS.items():
        assert len(sound) * 1000 / RATE <= MAX_MS, name
        assert float(np.abs(sound).max()) <= VOLUME + 1e-6, name  # never louder than today's cues
    names = list(SOUNDS)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            same_len = len(SOUNDS[a]) == len(SOUNDS[b])
            assert not (same_len and np.allclose(SOUNDS[a], SOUNDS[b])), (a, b)


def test_result_earcons_have_their_own_shape():
    pytest.importorskip("numpy")  # CI's server job has no voice group
    import numpy as np

    from artemis.voice.earcons import RATE, SOUNDS

    def pitch(chunk):  # dominant frequency of one note
        spectrum = np.abs(np.fft.rfft(chunk))
        return np.fft.rfftfreq(len(chunk), 1 / RATE)[int(spectrum.argmax())]

    def notes(name, count):
        return [pitch(c) for c in np.array_split(SOUNDS[name], count)]

    up = notes("success", 3)
    assert up[0] < up[1] < up[2]  # rising
    down = notes("failure", 2)
    assert down[0] > down[1] and max(down) < 660  # falling, and lower than every other cue
    knock = SOUNDS["approval"]
    third = len(knock) // 3
    assert float(np.abs(knock[third:2 * third]).max()) == 0.0  # the silent gap between the two knocks
class FakeDll:
    def __init__(self):
        self.calls = []

    def nvdaController_cancelSpeech(self):
        self.calls.append("cancel")

    def nvdaController_speakText(self, text):
        self.calls.append(("speak", text))

    def nvdaController_brailleMessage(self, text):
        self.calls.append(("braille", text))


def nvda_with_clock():
    from pathlib import Path as _P

    from artemis.voice.speech import NvdaSpeaker

    now = [100.0]
    speaker = NvdaSpeaker(dll_path=_P("missing.dll"), clock=lambda: now[0])
    speaker._dll = FakeDll()
    return speaker, now


def test_nvda_busy_estimates_from_word_count():
    s, now = nvda_with_clock()
    assert not s.busy()
    s.speak("one two three four five six seven", interrupt=False)  # 7 words at 3.5 a second: 2 s
    now[0] += 1.9
    assert s.busy()
    now[0] += 0.2
    assert not s.busy()


def test_nvda_queued_lines_add_up_and_interrupt_restarts():
    s, now = nvda_with_clock()
    s.speak("a b c d e f g", interrupt=False)  # 2 s
    s.speak("h i j k l m n", interrupt=False)  # queued behind it: done at 4 s
    now[0] += 3.0
    assert s.busy()
    s.speak("stop", interrupt=True)  # a high line cuts both off: about 0.3 s from now
    assert s._dll.calls[-3] == "cancel"
    now[0] += 0.5
    assert not s.busy()


def test_nvda_cancel_clears_busy():
    s, now = nvda_with_clock()
    s.speak("a long line of words that takes a while to say out loud", interrupt=False)
    s.cancel()
    assert not s.busy()


def test_low_line_dropped_while_nvda_still_talking():
    s, now = nvda_with_clock()
    a = Announcer(nvda=s, sapi=FakeSpeaker("sapi"), is_nvda_running=lambda: True)
    s.available = lambda: True
    asyncio.run(a.announce("main.py ran successfully. It printed: Hello, World!", "high"))
    asyncio.run(a.announce("Still working.", "low"))  # dropped: the result is still being read
    asyncio.run(a.announce("Created main.py, 1 line.", "normal"))  # normal is never dropped
    spoken = [c[1] for c in s._dll.calls if isinstance(c, tuple) and c[0] == "speak"]
    assert spoken == ["main.py ran successfully. It printed: Hello, World!", "Created main.py, 1 line."]

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            if inspect.signature(fn).parameters:  # pytest fixtures or parametrize: pytest only
                print("skipped (needs pytest)", name)
                continue
            fn()
            print("ok", name)
