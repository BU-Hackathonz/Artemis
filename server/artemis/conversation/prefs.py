"""Remembering how the developer likes to be answered, between sessions.

Only the answer style is stored (`verbosity` and `focus`, the two fields of `Style`). Never a
transcript, a project name, a path or anything else the developer said: this file must be safe to
open, copy or delete. A missing, unreadable or damaged file means the defaults, never an error, and
a failed save never reaches the conversation: the style still applies for this session.

Nothing here talks to a model, the network or a microphone.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from artemis.contracts import Style

log = logging.getLogger(__name__)

VERSION = 1
_VERBOSITY = ("brief", "normal", "detailed")
_FOCUS = ("none", "visual", "code")


def default_path() -> Path:
    """`ARTEMIS_PREFS_FILE` if set to an absolute path (a leading ~ is expanded), otherwise
    ~/.artemis/preferences.json. A relative value is ignored: the core runs in server/, so it would
    land inside the repo (Avery's review on #103)."""
    default = Path.home() / ".artemis" / "preferences.json"
    override = os.environ.get("ARTEMIS_PREFS_FILE", "").strip()
    if not override:
        return default
    path = Path(override).expanduser()
    return path if path.is_absolute() else default


def load_style(path: Path) -> Style:
    """The saved style, or the defaults when there is none or it cannot be trusted.

    Each field is checked on its own, so one bad value does not discard the other.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Style()
    if not isinstance(raw, dict) or raw.get("version") != VERSION:
        return Style()
    style = raw.get("style")
    if not isinstance(style, dict):
        return Style()
    verbosity = style.get("verbosity")
    focus = style.get("focus")
    return Style(
        verbosity=verbosity if verbosity in _VERBOSITY else "normal",
        focus=focus if focus in _FOCUS else "none",
    )


def save_style(path: Path, style: Style) -> bool:
    """Write the style atomically. True when it was saved; never raises."""
    payload = {"version": VERSION, "style": {"verbosity": style.verbosity, "focus": style.focus}}
    tmp_name = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".preferences-", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(tmp_name, path)
        return True
    except OSError:
        log.warning("could not save the answer style", exc_info=True)
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        return False
