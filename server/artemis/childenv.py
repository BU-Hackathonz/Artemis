"""Environments for child processes the core starts itself: its own tools and Playwright's browser.

User programs get the stricter allow-list in runner/local.py (child_env). These children need most of
the normal environment (PATH, the Windows profile and temp folders, where Playwright keeps its browsers),
so they get os.environ with the secrets removed. The names match policy/redact.py.
"""

import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

_SECRET_NAME = re.compile(r"^ANTHROPIC_|_(TOKEN|KEY|SECRET)$")  # ARTEMIS_TOKEN matches the suffix


def is_secret_name(name: str) -> bool:
    return bool(_SECRET_NAME.search(name.upper()))


def child_environ(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """A copy of `base` (default os.environ) without the API key, workspace ID and launch token."""
    source = os.environ if base is None else base
    return {name: value for name, value in source.items() if not is_secret_name(name)}


@contextmanager
def secrets_hidden_from_environ() -> Iterator[None]:
    """Playwright copies os.environ into its Node driver when the driver starts, and has no env= for it.

    Hide the secrets only while the driver starts, then put them back. Other threads read os.environ
    during this window too, so keep the block short and only around the start.
    """
    hidden = {name: value for name, value in os.environ.items() if is_secret_name(name)}
    for name in hidden:
        del os.environ[name]
    try:
        yield
    finally:
        os.environ.update(hidden)
