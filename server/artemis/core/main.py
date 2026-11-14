"""Builds the core from ARTEMIS_* environment variables. uvicorn target: artemis.core.main:app

ARTEMIS_TOKEN      required; infra/start.ps1 generates it (contracts.md §6)
ARTEMIS_AGENT      stub (default) | claude
ARTEMIS_VOICE      whisper (default when the voice group is installed) | stub
ARTEMIS_RUNNER     real (default) | fake (writes files, never runs anything)
ARTEMIS_CONVERSATION  off (default) | on: built-in spoken phrases and follow-ups (contracts.md §5.4)
ARTEMIS_PREFS_FILE    where conversation mode remembers the answer style; default
                      %USERPROFILE%/.artemis/preferences.json. Absolute, outside the projects folder
ARTEMIS_PROJECTS_DIR, ARTEMIS_LOG_DIR
"""

import functools
import logging
import os
from pathlib import Path

from artemis.agent.config import Config
from artemis.policy.redact import install_log_redaction

from .app import Core, create_app

log = logging.getLogger(__name__)


def make_voice():
    choice = os.environ.get("ARTEMIS_VOICE", "whisper")
    if choice == "whisper":
        try:
            from artemis.voice.listen import WhisperVoice

            return WhisperVoice()
        except ImportError:
            log.warning("voice group not installed (uv sync --group voice); using StubVoice")
    from artemis.voice import StubVoice

    return StubVoice()


def agent_factory(config: Config):
    try:
        from artemis.agent import make_agent  # PR #11; one agent per session
    except ImportError:
        from artemis.agent.stub import StubAgent

        if os.environ.get("ARTEMIS_AGENT") == "claude":
            # Don't fall back silently (demo runbook review): say it in the core log at startup.
            log.warning("ARTEMIS_AGENT=claude, but this build has no real agent yet; using StubAgent")
        return StubAgent

    client = None
    if config.agent == "claude":
        try:
            from artemis.agent.claude import make_client

            client = make_client(config)  # reads the API key and workspace here, once, at startup
        except ImportError:
            pass  # no SDK: make_agent fails on each new session, as it always has
    # The config and client come from startup, so a new session doesn't read the environment again.
    return functools.partial(make_agent, config, client=client)


def make_scan():
    try:
        from artemis.policy import scan  # PR #11 (Bandit)
    except ImportError:
        scan = None
    if scan is None or not scan.available():
        # Approvals then carry no scan result, so say it where the team will look.
        log.warning("Security scanner (Bandit) is not installed: writes and runs are not scanned. "
                    "Install it with: uv sync --group security")
        return None
    return scan.scan


def make_vocabulary():
    try:
        from artemis.agent.context import vocabulary  # PR #11
    except ImportError:
        return None
    return vocabulary


def runner_factory():
    if os.environ.get("ARTEMIS_RUNNER", "real") == "fake":
        from artemis.policy.testing import FakeRunner

        return FakeRunner
    from artemis.runner import LocalRunner

    return LocalRunner


REPO = Path(__file__).resolve().parents[3]


def prefs_path(projects_dir: Path) -> Path | None:
    """The file the answer style is remembered in, or None to remember nothing.

    Only with ARTEMIS_CONVERSATION=on (contracts.md §5.4): with it off nothing is read or written.
    The file must be an absolute path outside the projects folder (the agent writes there) and
    outside this repository (the core runs in server/, so a relative path would land in it).
    """
    if os.environ.get("ARTEMIS_CONVERSATION", "off").lower() != "on":
        return None
    from artemis.conversation.prefs import default_path

    path = default_path().expanduser()
    if not path.is_absolute():
        log.warning("ARTEMIS_PREFS_FILE must be an absolute path; the answer style won't be remembered")
        return None
    resolved = path.resolve()
    for root, name in ((Path(projects_dir), "projects folder"), (REPO, "Artemis repository")):
        if resolved.is_relative_to(root.resolve()):
            log.warning("ARTEMIS_PREFS_FILE is inside the %s; the answer style won't be remembered", name)
            return None
    return path


def build():
    install_log_redaction()  # core.log gets tracebacks too; uvicorn has configured logging by now
    config = Config.from_env()
    log_dir = Path(os.environ.get("ARTEMIS_LOG_DIR") or Path.home() / ".artemis" / "logs")
    core = Core(
        voice=make_voice(),
        make_runner=runner_factory(),
        make_agent=agent_factory(config),
        projects_dir=config.projects_dir,
        scan=make_scan(),
        log_dir=log_dir,
        vocabulary=make_vocabulary(),
        prefs_path=prefs_path(config.projects_dir),
    )
    return create_app(core, os.environ.get("ARTEMIS_TOKEN", ""))


app = build()
