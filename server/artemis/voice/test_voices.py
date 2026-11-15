"""Voice list tests: SAPI and ElevenLabs voices. No test touches the network: every fetch gets a fake opener."""

import asyncio
import json
import logging
import sys
import urllib.request
from urllib.error import HTTPError, URLError

import pytest

from artemis.voice import voices
from artemis.voice.voices import ELEVEN_VOICES_URL, available_voices, fetch_eleven_voices, list_sapi_voices

KEY = "fake_voice_list_value"


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_returning(body: bytes):
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        return FakeResponse(body)

    opener.calls = calls
    return opener


def sample_payload():
    return json.dumps({
        "voices": [
            {"voice_id": "v1", "name": "Rachel", "category": "premade", "preview_url": "https://x/a.mp3", "labels": {"a": "b"}},
            {"voice_id": "v2", "name": "Adam"},
        ],
    }).encode()


def test_fetch_returns_id_and_name_only_and_sends_key_in_header():
    opener = opener_returning(sample_payload())
    result = fetch_eleven_voices(KEY, opener=opener)

    assert result == [
        {"engine": "elevenlabs", "id": "v1", "name": "Rachel"},
        {"engine": "elevenlabs", "id": "v2", "name": "Adam"},
    ]
    assert len(opener.calls) == 1
    request, timeout = opener.calls[0]
    assert timeout == 3.0
    assert request.full_url == ELEVEN_VOICES_URL
    assert request.get_method() == "GET"
    assert request.get_header("Xi-api-key") == KEY
    assert KEY not in request.full_url
    assert request.data is None


def test_fetch_drops_extra_fields_and_malformed_entries():
    body = json.dumps({"voices": [
        {"voice_id": "ok", "name": "Good", "api_key": "leak"},
        {"voice_id": "", "name": "No id"},
        {"voice_id": "no-name"},
        {"voice_id": 7, "name": "Numeric id"},
        "not a dict",
        {"voice_id": "last", "name": "Last"},
    ]}).encode()
    result = fetch_eleven_voices(KEY, opener=opener_returning(body))
    assert result == [
        {"engine": "elevenlabs", "id": "ok", "name": "Good"},
        {"engine": "elevenlabs", "id": "last", "name": "Last"},
    ]
    assert all(set(v) == {"engine", "id", "name"} for v in result)


def test_fetch_caps_at_200_voices():
    body = json.dumps({"voices": [{"voice_id": f"v{i}", "name": f"N{i}"} for i in range(250)]}).encode()
    result = fetch_eleven_voices(KEY, opener=opener_returning(body))
    assert len(result) == 200
    assert result[-1]["id"] == "v199"


def test_fetch_without_key_makes_no_call():
    opener = opener_returning(sample_payload())
    assert fetch_eleven_voices("", opener=opener) == []
    assert opener.calls == []


def test_http_error_returns_empty_and_logs_status_only(caplog):
    def opener(request, timeout):
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    with caplog.at_level(logging.WARNING):
        assert fetch_eleven_voices(KEY, opener=opener) == []
    assert "401" in caplog.text
    assert KEY not in caplog.text


def test_url_error_returns_empty_and_logs_type_only(caplog):
    def opener(request, timeout):
        raise URLError(f"name resolution failed for {KEY}")

    with caplog.at_level(logging.WARNING):
        assert fetch_eleven_voices(KEY, opener=opener) == []
    assert "URLError" in caplog.text
    assert KEY not in caplog.text


def test_timeout_returns_empty_and_logs_type_only(caplog):
    def opener(request, timeout):
        raise TimeoutError("timed out")

    with caplog.at_level(logging.WARNING):
        assert fetch_eleven_voices(KEY, opener=opener) == []
    assert "TimeoutError" in caplog.text
    assert KEY not in caplog.text


def test_bad_json_returns_empty_and_logs_without_key(caplog):
    with caplog.at_level(logging.WARNING):
        assert fetch_eleven_voices(KEY, opener=opener_returning(b"<html>not json</html>")) == []
    assert "ValueError" in caplog.text or "JSONDecodeError" in caplog.text
    assert KEY not in caplog.text


def test_payload_without_voices_list_returns_empty(caplog):
    with caplog.at_level(logging.WARNING):
        assert fetch_eleven_voices(KEY, opener=opener_returning(b'{"voices": "nope"}')) == []
        assert fetch_eleven_voices(KEY, opener=opener_returning(b"[1, 2]")) == []
    assert KEY not in caplog.text


def test_no_key_skips_elevenlabs_without_network(monkeypatch):
    calls = []
    monkeypatch.setattr(voices, "fetch_eleven_voices", lambda key, **kw: calls.append(key) or [])

    result = asyncio.run(available_voices(
        sapi_list=lambda: [{"engine": "sapi", "id": "Zira", "name": "Zira"}],
        environ={},
    ))
    assert result == [{"engine": "sapi", "id": "Zira", "name": "Zira"}]
    assert calls == []


def test_key_and_eleven_list_order_elevenlabs_first_then_sapi():
    seen = []

    def eleven(key):
        seen.append(key)
        return [{"engine": "elevenlabs", "id": "v1", "name": "Rachel", "extra": "dropped"}]

    result = asyncio.run(available_voices(
        sapi_list=lambda: [{"engine": "sapi", "id": "Zira", "name": "Zira"}],
        eleven_list=eleven,
        environ={"ELEVENLABS_API_KEY": f"  {KEY}  "},
    ))
    assert seen == [KEY]
    assert result == [
        {"engine": "elevenlabs", "id": "v1", "name": "Rachel"},
        {"engine": "sapi", "id": "Zira", "name": "Zira"},
    ]


def test_elevenlabs_failure_still_returns_sapi_list(caplog):
    def opener(request, timeout):
        raise HTTPError(request.full_url, 500, "Server Error", {}, None)

    def eleven(key):
        return fetch_eleven_voices(key, opener=opener)

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(available_voices(
            sapi_list=lambda: [{"engine": "sapi", "id": "David", "name": "David"}],
            eleven_list=eleven,
            environ={"ELEVENLABS_API_KEY": KEY},
        ))
    assert result == [{"engine": "sapi", "id": "David", "name": "David"}]
    assert KEY not in caplog.text


def test_elevenlabs_raising_still_returns_sapi_list():
    def eleven(key):
        raise RuntimeError("boom")

    result = asyncio.run(available_voices(
        sapi_list=lambda: [{"engine": "sapi", "id": "David", "name": "David"}],
        eleven_list=eleven,
        environ={"ELEVENLABS_API_KEY": KEY},
    ))
    assert result == [{"engine": "sapi", "id": "David", "name": "David"}]


def test_sapi_entries_keep_only_engine_id_name():
    result = asyncio.run(available_voices(
        sapi_list=lambda: [{"engine": "sapi", "id": "Zira", "name": "Zira", "token": object()}],
        environ={},
    ))
    assert result == [{"engine": "sapi", "id": "Zira", "name": "Zira"}]


def test_non_windows_list_sapi_voices_is_empty(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert list_sapi_voices() == []


def test_windows_without_pywin32_is_empty(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "pythoncom", None)  # makes the lazy import fail
    assert list_sapi_voices() == []


def test_available_voices_runs_sapi_off_the_event_loop(monkeypatch):
    threads = []

    def sapi():
        import threading
        threads.append(threading.current_thread() is not threading.main_thread())
        return []

    asyncio.run(available_voices(sapi_list=sapi, environ={}))
    assert threads == [True]


def test_urlopen_is_not_called_by_available_voices_without_key(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("network call")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    assert asyncio.run(available_voices(sapi_list=lambda: [], environ={})) == []
