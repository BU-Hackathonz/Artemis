"""Remembering the answer style: the file, the controller hook and the reset phrase."""

import json

from artemis.contracts import Style
from artemis.conversation import Controller
from artemis.conversation.prefs import VERSION, default_path, load_style, save_style


def test_round_trip(tmp_path):
    path = tmp_path / "sub" / "preferences.json"
    assert save_style(path, Style(verbosity="brief", focus="code")) is True
    assert load_style(path) == Style(verbosity="brief", focus="code")


def test_missing_file_means_defaults(tmp_path):
    assert load_style(tmp_path / "nothing.json") == Style()


def test_damaged_files_mean_defaults(tmp_path):
    path = tmp_path / "p.json"
    for text in ["", "not json", "[]", "null", '{"version": 99, "style": {"verbosity": "brief"}}', '{"version": 1}']:
        path.write_text(text, encoding="utf-8")
        assert load_style(path) == Style()


def test_each_field_is_checked_on_its_own(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"version": VERSION, "style": {"verbosity": "loud", "focus": "code"}}))
    assert load_style(path) == Style(verbosity="normal", focus="code")
    path.write_text(json.dumps({"version": VERSION, "style": {"verbosity": "brief", "focus": 7}}))
    assert load_style(path) == Style(verbosity="brief", focus="none")


def test_only_the_style_is_written(tmp_path):
    path = tmp_path / "p.json"
    save_style(path, Style(verbosity="detailed", focus="visual"))
    assert json.loads(path.read_text()) == {"version": VERSION, "style": {"verbosity": "detailed", "focus": "visual"}}
    assert [p.name for p in tmp_path.iterdir()] == ["p.json"]  # no temp file left behind


def test_a_failed_save_returns_false_and_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert save_style(blocker / "preferences.json", Style(verbosity="brief")) is False  # parent is a file


def test_default_path_honors_the_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTEMIS_PREFS_FILE", str(tmp_path / "mine.json"))
    assert default_path() == tmp_path / "mine.json"
    monkeypatch.delenv("ARTEMIS_PREFS_FILE")
    assert default_path().name == "preferences.json" and default_path().parent.name == ".artemis"


def test_controller_starts_from_the_saved_style():
    c = Controller(style=Style(verbosity="brief", focus="visual"))
    assert c.style == Style(verbosity="brief", focus="visual")
    assert Controller().style == Style()


def test_controller_reports_every_style_change():
    seen = []
    c = Controller(on_style=seen.append)
    c.decide("keep it brief")
    c.decide("code mode")
    c.decide("deep dive")
    assert seen == [
        Style(verbosity="brief", focus="none"),
        Style(verbosity="brief", focus="code"),
        Style(verbosity="detailed", focus="code"),
    ]


def test_other_phrases_do_not_touch_the_style():
    seen = []
    c = Controller(on_style=seen.append)
    for said in ["status", "stop talking", "repeat that", "create a python program"]:
        c.decide(said)
    assert seen == []


def test_reset_phrase_clears_both_fields_and_says_so():
    seen = []
    c = Controller(style=Style(verbosity="brief", focus="code"), on_style=seen.append)
    d = c.decide("reset my preferences")
    assert d.intent == "reset_style" and d.action == "say" and "forgotten" in d.say
    assert c.style == Style() and seen == [Style()]


def test_reset_inside_a_sentence_goes_to_the_agent():
    c = Controller(style=Style(verbosity="brief"))
    d = c.decide("reset the preferences page in my app")
    assert d.intent == "request" and d.action == "submit"
    assert c.style == Style(verbosity="brief")


def test_a_failing_hook_never_breaks_the_conversation():
    def boom(_style):
        raise RuntimeError("disk full")

    c = Controller(on_style=boom)
    d = c.decide("keep it brief")
    assert d.action == "say" and c.style.verbosity == "brief"


def test_prefs_file_expands_home_and_ignores_a_relative_path(monkeypatch, tmp_path):
    from artemis.conversation.prefs import default_path

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("ARTEMIS_PREFS_FILE", "~/prefs.json")
    assert default_path() == tmp_path / "prefs.json"
    monkeypatch.setenv("ARTEMIS_PREFS_FILE", "prefs.json")  # relative: would land in server/
    assert default_path() == tmp_path / ".artemis" / "preferences.json"
    monkeypatch.delenv("ARTEMIS_PREFS_FILE")
    assert default_path() == tmp_path / ".artemis" / "preferences.json"
