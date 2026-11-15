"""voice.setup downloads both Whisper models (no network: faster_whisper is faked)."""

import sys
import types

from artemis.voice import setup


def _fake_whisper(monkeypatch):
    made = []
    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = lambda name, **kw: made.append((name, kw))
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    return made


def test_setup_fetches_the_main_and_the_quick_model(monkeypatch, capsys):
    monkeypatch.delenv("ARTEMIS_WHISPER_MODEL", raising=False)
    monkeypatch.delenv("ARTEMIS_WAKE_QUICK_MODEL", raising=False)
    made = _fake_whisper(monkeypatch)
    setup.fetch_model()
    assert [m for m, _ in made] == ["small.en", "tiny.en"]
    assert all(kw == {"device": "cpu", "compute_type": "int8"} for _, kw in made)
    assert "tiny.en ready" in capsys.readouterr().out


def test_setup_skips_the_quick_model_when_off_or_the_same(monkeypatch):
    monkeypatch.delenv("ARTEMIS_WHISPER_MODEL", raising=False)
    monkeypatch.setenv("ARTEMIS_WAKE_QUICK_MODEL", "off")
    assert setup.model_names() == ["small.en"]
    monkeypatch.delenv("ARTEMIS_WAKE_QUICK_MODEL")
    monkeypatch.setenv("ARTEMIS_WHISPER_MODEL", "tiny.en")
    assert setup.model_names() == ["tiny.en"]
