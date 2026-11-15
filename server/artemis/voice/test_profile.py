import json
import logging
import os
import stat
import sys
from pathlib import Path

import pytest

from artemis.voice import profile as vp
from artemis.voice.profile import VoiceProfile, as_dict, default_path, load, save

LOGGER = "artemis.voice.profile"
FAKE_KEY = "test-not-a-real-key-123"
GOOD = {
    "engine": "sapi",
    "eleven_voice_id": "abc_123-XYZ",
    "eleven_speed": 1.1,
    "sapi_voice": "Zira",
    "sapi_rate": 2,
    "working_cue": True,
}


def warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and r.name == LOGGER]


def write(path: Path, data) -> Path:
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def test_defaults_when_there_is_no_file(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        p = load(tmp_path / "missing.json", environ={})
    assert p == VoiceProfile()
    assert as_dict(p) == {
        "engine": "elevenlabs",
        "eleven_voice_id": None,
        "eleven_speed": 1.0,
        "sapi_voice": None,
        "sapi_rate": None,
        "working_cue": False,
    }
    assert warnings(caplog) == []  # a missing file is normal


def test_default_path_and_override(tmp_path):
    assert default_path(environ={}) == Path.home() / ".artemis" / "voice.json"
    assert default_path(environ={"ARTEMIS_VOICE_PROFILE": ""}) == Path.home() / ".artemis" / "voice.json"
    custom = tmp_path / "mine.json"
    assert default_path(environ={"ARTEMIS_VOICE_PROFILE": str(custom)}) == custom


@pytest.mark.parametrize(
    "key, bad",
    [
        ("engine", "bogus"),
        ("engine", 3),
        ("eleven_voice_id", "has space"),
        ("eleven_voice_id", "x" * 65),
        ("eleven_voice_id", 5),
        ("eleven_speed", 1.5),
        ("eleven_speed", 0.2),
        ("eleven_speed", True),
        ("eleven_speed", "fast"),
        ("sapi_voice", ""),
        ("sapi_voice", "y" * 129),
        ("sapi_voice", 7),
        ("sapi_rate", 7),
        ("sapi_rate", -4),
        ("sapi_rate", True),
        ("sapi_rate", 2.0),
        ("working_cue", "earcon"),
        ("working_cue", 1),
    ],
)
def test_each_bad_value_is_ignored_with_one_warning_and_the_rest_kept(tmp_path, caplog, key, bad):
    path = write(tmp_path / "voice.json", {**GOOD, key: bad})
    expected = VoiceProfile(**{k: v for k, v in GOOD.items() if k != key})
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        p = load(path, environ={})
    assert getattr(p, key) == getattr(VoiceProfile(), key)
    assert p == expected
    msgs = warnings(caplog)
    assert len(msgs) == 1 and key in msgs[0]


def test_placeholder_and_empty_voice_id_in_file_mean_unset(tmp_path, caplog):
    path = write(tmp_path / "voice.json", {"eleven_voice_id": "<PASTE_VOICE_ID>"})
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert load(path, environ={}).eleven_voice_id is None
    assert warnings(caplog) == []


def test_bad_value_warning_never_shows_the_value(tmp_path, caplog):
    path = write(tmp_path / "voice.json", {"eleven_voice_id": f"{FAKE_KEY} with spaces"})
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        load(path, environ={})
    assert warnings(caplog) and FAKE_KEY not in caplog.text


@pytest.mark.parametrize("text", ["{not json", "", "[1, 2]", '"just a string"', "42"])
def test_broken_or_non_object_json_gives_defaults_without_crashing(tmp_path, caplog, text):
    path = write(tmp_path / "voice.json", text)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert load(path, environ={}) == VoiceProfile()
    assert len(warnings(caplog)) == 1


def test_non_utf8_file_gives_defaults(tmp_path, caplog):
    path = tmp_path / "voice.json"
    path.write_bytes(b"\xff\xfe\x00garbage")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert load(path, environ={}) == VoiceProfile()
    assert len(warnings(caplog)) == 1


def test_a_directory_in_place_of_the_file_gives_defaults(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert load(tmp_path, environ={}) == VoiceProfile()
    assert len(warnings(caplog)) == 1


def test_unknown_keys_are_ignored_with_a_warning_and_their_values_not_logged(tmp_path, caplog):
    path = write(tmp_path / "voice.json", {"engine": "nvda", "mystery": FAKE_KEY})
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        p = load(path, environ={})
    assert p.engine == "nvda"
    assert warnings(caplog) and FAKE_KEY not in caplog.text


def test_env_overrides_the_file(tmp_path):
    path = write(tmp_path / "voice.json", {"engine": "sapi", "eleven_speed": 1.1, "working_cue": False})
    env = {
        "ARTEMIS_VOICE_ENGINE": " NVDA ",
        "ARTEMIS_ELEVEN_VOICE_ID": "voice_1",
        "ARTEMIS_ELEVEN_SPEED": "0.9",
        "ARTEMIS_SAPI_VOICE": "Hazel",
        "ARTEMIS_SAPI_RATE": "-2",
        "ARTEMIS_WORKING_CUE": "earcon",
    }
    p = load(path, environ=env)
    assert p == VoiceProfile(
        engine="nvda", eleven_voice_id="voice_1", eleven_speed=0.9,
        sapi_voice="Hazel", sapi_rate=-2, working_cue=True,
    )


def test_env_works_without_a_file(tmp_path):
    assert load(tmp_path / "none.json", environ={"ARTEMIS_SAPI_RATE": "6"}).sapi_rate == 6


@pytest.mark.parametrize("raw, expected", [("off", False), ("spoken", False), ("SPOKEN", False), ("Earcon", True)])
def test_working_cue_env_words(tmp_path, raw, expected):
    path = write(tmp_path / "voice.json", {"working_cue": not expected})
    assert load(path, environ={"ARTEMIS_WORKING_CUE": raw}).working_cue is expected


@pytest.mark.parametrize(
    "env",
    [
        {"ARTEMIS_WORKING_CUE": ""},
        {"ARTEMIS_WORKING_CUE": "   "},
        {"ARTEMIS_ELEVEN_VOICE_ID": ""},
        {"ARTEMIS_ELEVEN_VOICE_ID": "<PASTE_VOICE_ID>"},
        {"ARTEMIS_ELEVEN_SPEED": ""},
        {"ARTEMIS_SAPI_RATE": ""},
        {"ARTEMIS_SAPI_VOICE": ""},
        {"ARTEMIS_VOICE_ENGINE": ""},
    ],
)
def test_empty_or_placeholder_env_does_not_override(tmp_path, env):
    path = write(tmp_path / "voice.json", {**GOOD})
    assert load(path, environ=env) == VoiceProfile(**GOOD)


def test_bad_env_values_warn_and_keep_the_file_value(tmp_path, caplog):
    path = write(tmp_path / "voice.json", dict(GOOD))
    env = {
        "ARTEMIS_VOICE_ENGINE": "dragon",
        "ARTEMIS_ELEVEN_SPEED": "nan",
        "ARTEMIS_SAPI_RATE": "2.5",
        "ARTEMIS_WORKING_CUE": "loud",
        "ARTEMIS_ELEVEN_VOICE_ID": f"{FAKE_KEY} with spaces",
    }
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        p = load(path, environ=env)
    assert p == VoiceProfile(**GOOD)
    msgs = warnings(caplog)
    assert len(msgs) == 5
    for var in env:
        assert any(var in m for m in msgs)
    assert FAKE_KEY not in caplog.text and "dragon" not in caplog.text


def test_env_range_checks_match_the_file(tmp_path):
    for raw in ("0.69", "1.21", "inf"):
        assert load(tmp_path / "none.json", environ={"ARTEMIS_ELEVEN_SPEED": raw}).eleven_speed == 1.0
    for raw in ("-4", "7"):
        assert load(tmp_path / "none.json", environ={"ARTEMIS_SAPI_RATE": raw}).sapi_rate is None


def test_round_trip(tmp_path):
    original = VoiceProfile(
        engine="sapi", eleven_voice_id="a1_b2", eleven_speed=0.8,
        sapi_voice="Zira", sapi_rate=-1, working_cue=True,
    )
    path = tmp_path / "artemis" / "voice.json"
    save(original, path)
    assert load(path, environ={}) == original


def test_saved_file_has_only_the_profile_fields(tmp_path):
    path = tmp_path / "voice.json"
    save(VoiceProfile(), path)
    assert set(json.loads(path.read_text(encoding="utf-8"))) == set(as_dict(VoiceProfile()))


def test_the_api_key_is_never_written(tmp_path):
    env = {"ELEVENLABS_API_KEY": FAKE_KEY, "ARTEMIS_ELEVEN_VOICE_ID": "voice_1"}
    path = tmp_path / "voice.json"
    p = load(path, environ=env)
    save(p, path)
    raw = path.read_bytes()
    assert FAKE_KEY.encode() not in raw
    assert b"ELEVENLABS_API_KEY" not in raw
    assert not any(_is_secret_name(k) for k in as_dict(p))
    assert FAKE_KEY not in repr(p)


def _is_secret_name(name: str) -> bool:
    return any(word in name.lower() for word in ("key", "token", "secret"))


def test_save_refuses_an_invalid_value(tmp_path):
    with pytest.raises(ValueError):
        save(VoiceProfile(eleven_speed=5.0), tmp_path / "voice.json")
    assert not (tmp_path / "voice.json").exists()


def test_atomic_write_leaves_no_temp_files(tmp_path):
    path = tmp_path / "sub" / "voice.json"
    save(VoiceProfile(sapi_rate=1), path)
    save(VoiceProfile(sapi_rate=2), path)
    assert sorted(os.listdir(path.parent)) == ["voice.json"]
    assert load(path, environ={}).sapi_rate == 2


def test_failed_replace_cleans_up_and_keeps_the_old_file(tmp_path, monkeypatch):
    path = tmp_path / "voice.json"
    save(VoiceProfile(sapi_rate=1), path)

    def boom(src, dst):
        raise OSError("disk gone")

    monkeypatch.setattr(vp.os, "replace", boom)
    with pytest.raises(OSError):
        save(VoiceProfile(sapi_rate=5), path)
    assert sorted(os.listdir(tmp_path)) == ["voice.json"]
    assert load(path, environ={}).sapi_rate == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits; Windows ignores mkdir mode")
def test_new_parent_directory_is_private(tmp_path):
    path = tmp_path / "new" / "voice.json"
    save(VoiceProfile(), path)
    assert stat.S_IMODE(os.stat(path.parent).st_mode) & 0o077 == 0
