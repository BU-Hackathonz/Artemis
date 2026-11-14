"""Strip secrets from anything logged, spoken or sent to the model (design D4)."""

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
_ASSIGNMENT = re.compile(r"(?i)\b(api[_-]?key|password|secret)(\s*[:=]\s*)\S+")


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
    text = _ASSIGNMENT.sub(rf"\1\2{REDACTED}", text)
    return text


def redact_obj(value: Any) -> Any:
    """Redact every string inside nested dicts and lists."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: redact_obj(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_obj(item) for item in value]
    return value
