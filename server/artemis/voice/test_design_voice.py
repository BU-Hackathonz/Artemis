import base64
import io
import json
import urllib.error

import pytest

from artemis.voice import design_voice as dv

KEY = "fake_design_value"


class Reply:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Opener:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.requests = payload, error, []

    def __call__(self, request, timeout):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return Reply(self.payload)


def test_preview_text_fits_the_api_limits():
    assert 100 <= len(dv.PREVIEW_TEXT) <= 1000


def test_design_writes_previews_and_sends_the_key_only_in_the_header(tmp_path):
    audio = base64.b64encode(b"ID3fake").decode()
    opener = Opener({"previews": [{"generated_voice_id": "g1", "audio_base_64": audio},
                                  {"generated_voice_id": "g2", "audio_base_64": audio}], "text": "x"})
    ids = dv.design("calm voice", tmp_path, KEY, opener)
    assert ids == ["g1", "g2"]
    assert (tmp_path / "preview-1.mp3").read_bytes() == b"ID3fake"
    request = opener.requests[0]
    assert request.get_header("Xi-api-key") == KEY
    assert KEY not in request.full_url and KEY.encode() not in request.data
    body = json.loads(request.data)
    assert body == {"voice_description": "calm voice", "text": dv.PREVIEW_TEXT}


def test_create_returns_the_voice_id():
    opener = Opener({"voice_id": "v123", "name": "Artemis"})
    assert dv.create("g1", "Artemis", "calm voice", KEY, opener) == "v123"
    body = json.loads(opener.requests[0].data)
    assert body == {"voice_name": "Artemis", "voice_description": "calm voice", "generated_voice_id": "g1"}


def test_errors_name_the_status_and_never_the_key(capsys, monkeypatch):
    error = urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(b'{"detail": {"message": "Invalid API key"}}'))
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    monkeypatch.setattr(dv.urllib.request, "urlopen", Opener(error=error))
    assert dv.main(["create", "g1"]) == 1
    err = capsys.readouterr().err
    assert "HTTP 401: Invalid API key" in err and KEY not in err


def test_network_failure_gives_the_type_only():
    with pytest.raises(dv.ApiError, match="^URLError$"):
        dv.design("x", None, KEY, Opener(error=urllib.error.URLError("offline")))


def test_no_key_is_a_clear_message(capsys, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert dv.main(["design"]) == 1
    assert "ELEVENLABS_API_KEY is not set" in capsys.readouterr().err
