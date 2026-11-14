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


class Answers:
    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.answers.pop(0)


class Sequence:
    """An opener that returns each payload in turn (design, then create)."""

    def __init__(self, *payloads):
        self.payloads, self.requests = list(payloads), []

    def __call__(self, request, timeout):
        self.requests.append(request)
        return Reply(self.payloads.pop(0))


def _previews(*ids):
    audio = base64.b64encode(b"ID3").decode()
    return {"previews": [{"generated_voice_id": i, "audio_base_64": audio} for i in ids], "text": "x"}


def test_wizard_plays_each_preview_saves_the_pick_and_remembers_it(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    played, remembered = [], []
    opener = Sequence(_previews("g1", "g2", "g3"), {"voice_id": "v9"})
    answers = Answers("", "", "", "x", "2", "y")  # Enter x3, a wrong choice (asked again, no new previews), 2, yes
    code = dv.wizard(answers, played.append, lambda v: remembered.append(v) or True, opener, tmp_path)
    assert code == 0 and [p.name for p in played] == ["preview-1.mp3", "preview-2.mp3", "preview-3.mp3"]
    assert len(opener.requests) == 2  # one design, one create: a wrong answer costs no credits
    assert json.loads(opener.requests[1].data)["generated_voice_id"] == "g2"
    assert remembered == ["v9"]


def test_wizard_redo_makes_new_previews_with_the_change(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    opener = Sequence(_previews("a1"), _previews("b1"), {"voice_id": "v1"})
    answers = Answers("", "r", "a little deeper", "", "1", "n")
    assert dv.wizard(answers, lambda p: None, lambda v: True, opener, tmp_path) == 0
    second = json.loads(opener.requests[1].data)["voice_description"]
    assert second.endswith("a little deeper") and json.loads(opener.requests[2].data)["generated_voice_id"] == "b1"


def test_wizard_asks_for_the_key_hidden_when_it_is_not_set(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(dv.getpass, "getpass", lambda prompt: KEY)
    opener = Sequence(_previews("g1"), {"voice_id": "v1"})
    dv.wizard(Answers("", "1", "n"), lambda p: None, lambda v: True, opener, tmp_path)
    assert opener.requests[0].get_header("Xi-api-key") == KEY


class AudioOpener:
    """Returns mp3 bytes per request; voice ids in `fail` get HTTP 402 (a plan that can't use them)."""

    def __init__(self, fail=()):
        self.fail, self.requests = set(fail), []

    def __call__(self, request, timeout):
        self.requests.append(request)
        if any(v in request.full_url for v in self.fail):
            raise urllib.error.HTTPError(request.full_url, 402, "paid", {}, io.BytesIO(b'{"detail": {"message": "paid_plan_required"}}'))

        class Body:
            def read(self_inner):
                return b"ID3sample"

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

        return Body()


def test_pick_plays_each_sample_and_saves_the_chosen_voice_id(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    played, remembered = [], []
    opener = AudioOpener(fail={dv.CANDIDATES[1][1]})  # the second voice needs a paid plan
    n = len(dv.CANDIDATES) - 1
    answers = Answers(*([""] * n), "9", "1", "y")  # Enter for each, an out-of-range choice, then 1, then yes
    assert dv.pick(answers, played.append, lambda v: remembered.append(v) or True, opener, tmp_path) == 0
    assert len(played) == n and remembered == [dv.CANDIDATES[0][1]]
    assert len(opener.requests) == len(dv.CANDIDATES)  # one request per voice; asking again costs nothing
    request = opener.requests[0]
    assert request.get_header("Xi-api-key") == KEY and KEY not in request.full_url
    assert json.loads(request.data) == {"text": dv.SAMPLE_LINE, "model_id": "eleven_flash_v2_5"}


def test_pick_with_no_usable_voice_saves_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    opener = AudioOpener(fail={v for _, v, _ in dv.CANDIDATES})
    assert dv.pick(Answers(), lambda p: None, lambda v: pytest.fail("nothing to save"), opener, tmp_path) == 1
