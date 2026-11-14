"""Strip secrets from anything logged, spoken or sent to the model (design D4)."""

import logging
import os
import re
from typing import Any

REDACTED = "[redacted]"

_ENV_NAME = re.compile(r"^ANTHROPIC_|_(TOKEN|KEY|SECRET)$")
_MIN_ENV_VALUE = 6

_PEM = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)
_ANTHROPIC_KEY = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
_BEARER = re.compile(r"\bBearer\s+\S+")
# Header form, any case: "Authorization: bearer <value>".
_AUTH_BEARER = re.compile(r"(?i)(\bauthorization\s*[:=]\s*bearer\s+)\S+")
# Keys end in api_key / apikey / api-key (so x-api-key matches), token, password or secret,
# optionally with a prefix such as access_token or ARTEMIS_TOKEN. The keyword must end right
# before the separator, so "max_tokens=" and "token budget" are left alone.
_ASSIGNMENT = re.compile(
    r"""(?i)(?<![A-Za-z0-9])((?:[A-Za-z0-9]+[_-])*(?:api[_-]?key|token|password|passwd|secret))"""
    r"""(["']?\s*[:=]\s*["']?)[^\s&"',;]+"""
)


def _env_secrets() -> list[str]:
    values = {
        value
        for name, value in os.environ.items()
        if _ENV_NAME.search(name.upper()) and len(value) >= _MIN_ENV_VALUE
    }
    # Longest first, so a secret that contains another is replaced whole.
    return sorted(values, key=len, reverse=True)


def redact(text: str) -> str:
    for secret in _env_secrets():
        text = text.replace(secret, REDACTED)
    text = _PEM.sub(REDACTED, text)
    text = _ANTHROPIC_KEY.sub(REDACTED, text)
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    text = _AUTH_BEARER.sub(rf"\1{REDACTED}", text)
    text = _ASSIGNMENT.sub(rf"\1\2{REDACTED}", text)
    return text


class RedactFilter(logging.Filter):
    """Redacts the message and the traceback of every record before a handler formats it.

    Attach it to handlers, not loggers: a logger filter does not see records that
    propagate up from child loggers such as artemis.core.session.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = redact(record.getMessage())
            if record.exc_info:
                exc_text = redact(logging.Formatter().formatException(record.exc_info))
            else:
                exc_text = redact(record.exc_text) if record.exc_text else None
            stack = redact(record.stack_info) if record.stack_info else None
        except Exception:  # never let a logging bug write an unredacted record
            message, exc_text, stack = "log record suppressed: redaction failed", None, None
        record.msg = message
        record.args = ()
        record.exc_info = None
        record.exc_text = exc_text
        record.stack_info = stack
        return True


def install_log_redaction() -> None:
    """Add RedactFilter to every handler that exists now (root, uvicorn, artemis.*).

    Call after logging is configured. If the root logger has no handler, add one on stderr,
    which is what logging.lastResort would have written to anyway.
    """
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    loggers = [root] + [lg for lg in logging.root.manager.loggerDict.values() if isinstance(lg, logging.Logger)]
    for handler in {h for lg in loggers for h in lg.handlers}:
        if not any(isinstance(f, RedactFilter) for f in handler.filters):
            handler.addFilter(RedactFilter())


def redact_obj(value: Any) -> Any:
    """Redact every string inside nested dicts and lists."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: redact_obj(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_obj(item) for item in value]
    return value
