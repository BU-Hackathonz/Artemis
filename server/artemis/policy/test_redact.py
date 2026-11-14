"""`redact` on every pattern in design D4. The secrets here are made up."""

import io
import logging

import pytest

from artemis.policy.redact import REDACTED, RedactFilter, install_log_redaction, redact, redact_obj

FAKE_KEY = "sk-ant-api03-FAKEfake_1234-abcdEFGH"


def test_anthropic_key_pattern():
    out = redact(f"the key is {FAKE_KEY}.")
    assert "FAKEfake" not in out and REDACTED in out


@pytest.mark.parametrize(
    "name", ["ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "GITHUB_TOKEN", "ARTEMIS_TOKEN", "STRIPE_KEY", "APP_SECRET"]
)
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
    [
        "api_key=abc123",
        "API-KEY: abc123",
        "apikey = abc123",
        "password=abc123",
        "Secret: abc123",
        "token=abc123",
        "TOKEN: abc123",
        "access_token=abc123",
        "ARTEMIS_TOKEN=abc123",
        "x-api-key: abc123",
        "X-API-Key: abc123",
        "passwd=abc123",
    ],
)
def test_assignment_patterns(text):
    out = redact(text)
    assert "abc123" not in out and out.endswith(REDACTED)


def test_token_in_url_stops_at_the_next_parameter():
    assert redact("GET /x?token=abc123&page=2") == f"GET /x?token={REDACTED}&page=2"


def test_quoted_json_token_is_redacted():
    assert redact('{"token": "abc123"}') == f'{{"token": "{REDACTED}"}}'


@pytest.mark.parametrize(
    "text",
    ["Authorization: bearer abc.def-123", "authorization: Bearer abc.def-123", "AUTHORIZATION: BEARER abc.def-123"],
)
def test_authorization_bearer_any_case(text):
    out = redact(text)
    assert "abc.def-123" not in out and out.endswith(REDACTED)


def test_ordinary_text_is_unchanged():
    text = 'print("Hello, World!")\nmain.py ran successfully.'
    assert redact(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "token budget is 4000 words",
        "key: value",
        "the max_tokens=4096 setting",
        "tokenizer=whitespace",
        "total_tokens: 12",
        "mykey: value",
        "cache_key = build(x)",
    ],
)
def test_near_miss_text_is_not_mangled(text):
    # The keyword must end right before the separator; these only look similar.
    assert redact(text) == text


def test_logged_exception_message_and_traceback_are_redacted():
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(RedactFilter())
    log = logging.getLogger("artemis.test_redact_traceback")
    log.addHandler(handler)
    log.propagate = False
    try:
        raise RuntimeError(f"upstream rejected token={FAKE_KEY}")
    except RuntimeError:
        log.exception("agent crashed for %s", f"x-api-key: {FAKE_KEY}")
    finally:
        log.removeHandler(handler)
    text = buf.getvalue()
    assert "FAKEfake" not in text
    assert "agent crashed" in text and "Traceback" in text and "RuntimeError" in text
    assert REDACTED in text


def test_install_log_redaction_covers_existing_handlers():
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log = logging.getLogger("artemis.test_redact_install")
    log.addHandler(handler)
    try:
        install_log_redaction()
        install_log_redaction()  # idempotent: no second filter on the same handler
        assert sum(isinstance(f, RedactFilter) for f in handler.filters) == 1
        log.warning("saw %s", FAKE_KEY)
        assert "FAKEfake" not in buf.getvalue() and REDACTED in buf.getvalue()
    finally:
        log.removeHandler(handler)


def test_redact_obj_walks_nested_data():
    data = {"a": [f"x {FAKE_KEY}", {"b": "password=hunter22", "n": 3}], "ok": True}
    out = redact_obj(data)
    assert out == {"a": [f"x {REDACTED}", {"b": f"password={REDACTED}", "n": 3}], "ok": True}
