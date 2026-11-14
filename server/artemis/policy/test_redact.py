"""`redact` on every pattern in design D4. The secrets here are made up."""

import pytest

from artemis.policy.redact import REDACTED, redact, redact_obj

FAKE_KEY = "sk-ant-api03-FAKEfake_1234-abcdEFGH"


def test_anthropic_key_pattern():
    out = redact(f"the key is {FAKE_KEY}.")
    assert "FAKEfake" not in out and REDACTED in out


@pytest.mark.parametrize("name", ["ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "GITHUB_TOKEN", "STRIPE_KEY", "APP_SECRET"])
def test_env_value_is_redacted(monkeypatch, name):
    monkeypatch.setenv(name, "zq81-plain-value")
    assert redact("saw zq81-plain-value in output") == f"saw {REDACTED} in output"


def test_short_env_values_are_left_alone(monkeypatch):
    monkeypatch.setenv("SHORT_KEY", "abc")
    assert redact("abc def") == "abc def"


def test_unrelated_env_values_are_left_alone(monkeypatch):
    monkeypatch.setenv("FAVORITE_COLOR", "ultramarine")
    assert redact("ultramarine") == "ultramarine"


def test_bearer_token():
    assert redact("Authorization: Bearer abc.def-123") == f"Authorization: Bearer {REDACTED}"


def test_pem_private_key_block():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nIBAAKC\n-----END RSA PRIVATE KEY-----"
    assert redact(f"before\n{pem}\nafter") == f"before\n{REDACTED}\nafter"


def test_unterminated_pem_block_is_redacted_to_the_end():
    assert "MIIEow" not in redact("-----BEGIN PRIVATE KEY-----\nMIIEow")


@pytest.mark.parametrize(
    "text",
    ["api_key=abc123", "API-KEY: abc123", "apikey = abc123", "password=abc123", "Secret: abc123"],
)
def test_assignment_patterns(text):
    out = redact(text)
    assert "abc123" not in out and out.endswith(REDACTED)


def test_ordinary_text_is_unchanged():
    text = 'print("Hello, World!")\nmain.py ran successfully.'
    assert redact(text) == text


def test_redact_obj_walks_nested_data():
    data = {"a": [f"x {FAKE_KEY}", {"b": "password=hunter22", "n": 3}], "ok": True}
    out = redact_obj(data)
    assert out == {"a": [f"x {REDACTED}", {"b": f"password={REDACTED}", "n": 3}], "ok": True}
