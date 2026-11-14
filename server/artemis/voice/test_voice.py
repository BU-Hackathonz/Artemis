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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
