"""Voice tests. Run with pytest, or without it: python -m artemis.voice.test_voice (from server/)."""

import asyncio

from artemis.voice import Announcer, StubVoice, strip_wake_word, vocabulary_prompt


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
    assert strip_wake_word("  ARTEMIS run it") == "run it"
    assert strip_wake_word("run artemis.py") == "run artemis.py"  # only a leading wake word
    assert strip_wake_word("Artemisia is a plant") == "Artemisia is a plant"
    assert strip_wake_word("Artemis") == ""


def test_vocabulary_prompt_dedupes_and_caps():
    assert vocabulary_prompt(["main.py", "Main.py", " ", "LoginForm"]) == "main.py, LoginForm"
    p = vocabulary_prompt([f"word{i}" for i in range(500)], max_chars=50)
    assert len(p) <= 50 and p.startswith("word0, word1")


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
    v.set_vocabulary(["main.py"])
    assert v.prompt == "main.py"


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


def test_faster_and_slower_change_sapi_rate_and_say_so():
    sapi = FakeSapi(rate=1)
    a = Announcer(FakeSpeaker("nvda"), sapi, is_nvda_running=lambda: False)
    assert asyncio.run(a.set_rate(1)) is True and sapi.rate == 2
    assert asyncio.run(a.set_rate(-1)) is True and sapi.rate == 1
    assert [t for t, _ in sapi.said] == ["Speaking faster.", "Speaking slower."]


def test_rate_is_bounded_and_says_when_at_the_limit():
    from artemis.voice.speech import RATE_MAX, RATE_MIN

    sapi = FakeSapi(rate=RATE_MAX)
    a = Announcer(FakeSpeaker("nvda"), sapi, is_nvda_running=lambda: False)
    assert asyncio.run(a.set_rate(1)) is False and sapi.rate == RATE_MAX
    sapi.rate = RATE_MIN
    assert asyncio.run(a.set_rate(-1)) is False and sapi.rate == RATE_MIN
    assert [t for t, _ in sapi.said] == ["That's the fastest I go.", "That's the slowest I go."]


def test_rate_never_changes_under_nvda():
    nvda, sapi = FakeSpeaker("nvda"), FakeSapi(rate=1)
    a = Announcer(nvda, sapi, is_nvda_running=lambda: True)
    assert asyncio.run(a.set_rate(1)) is False
    assert sapi.rate == 1 and sapi.said == []
    assert "NVDA's speech settings" in nvda.said[0][0]


def test_env_rate_is_optional_and_safe():
    import os

    from artemis.voice.speech import RATE_MAX, _env_rate

    old = os.environ.get("ARTEMIS_SAPI_RATE")
    try:
        os.environ.pop("ARTEMIS_SAPI_RATE", None)
        assert _env_rate() is None  # unset: keep the user's own Windows rate
        os.environ["ARTEMIS_SAPI_RATE"] = "99"
        assert _env_rate() == RATE_MAX
        os.environ["ARTEMIS_SAPI_RATE"] = "fast"
        assert _env_rate() is None
    finally:
        if old is None:
            os.environ.pop("ARTEMIS_SAPI_RATE", None)
        else:
            os.environ["ARTEMIS_SAPI_RATE"] = old


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
    sapi = FakeSapi(rate=0)
    v = StubVoice(announcer=Announcer(FakeSpeaker("nvda"), sapi, is_nvda_running=lambda: False))
    assert asyncio.run(v.set_rate(1)) is True and sapi.rate == 1


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
