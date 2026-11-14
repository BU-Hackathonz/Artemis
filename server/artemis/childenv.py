"""Environments for child processes the core starts itself: its own tools and Playwright's browser.

User programs get the stricter allow-list in runner/local.py (child_env). These children need most of
the normal environment (PATH, the Windows profile and temp folders, where Playwright keeps its browsers),
so they get os.environ with the secrets removed. The names match policy/redact.py.
"""

import os
import re
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

_SECRET_NAME = re.compile(r"^ANTHROPIC_|_(TOKEN|KEY|SECRET)$")  # ARTEMIS_TOKEN matches the suffix

# Several callers can overlap (threads, or nested use). The first one in hides the secrets, the
# last one out restores them; the others only count. _hidden keeps the values while they are hidden.
_lock = threading.Lock()
_depth = 0
_hidden: dict[str, str] = {}


def is_secret_name(name: str) -> bool:
    return bool(_SECRET_NAME.search(name.upper()))


def child_environ(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """A copy of `base` (default os.environ) without the API key, workspace ID and launch token."""
    source = os.environ if base is None else base
    return {name: value for name, value in source.items() if not is_secret_name(name)}


def hidden_secret_values() -> list[str]:
    """The secret values that secrets_hidden_from_environ() has removed right now, so redaction can still see them."""
    with _lock:
        return list(_hidden.values())


@contextmanager
def secrets_hidden_from_environ() -> Iterator[None]:
    """Playwright copies os.environ into its Node driver when the driver starts, and has no env= for it.

    Hide the secrets only while the driver starts, then put them back. Other threads read os.environ
    during this window too, so keep the block short and only around the start. Blocks may overlap:
    the secrets come back only when the last block exits, even if it is an exception that ends it.
    """
    global _depth
    with _lock:
        if _depth == 0:
            _hidden.update({name: value for name, value in os.environ.items() if is_secret_name(name)})
            for name in _hidden:
                os.environ.pop(name, None)
        _depth += 1
    try:
        yield
    finally:
        with _lock:
            _depth -= 1
            if _depth == 0:
                os.environ.update(_hidden)
                _hidden.clear()
