"""Builds the core from ARTEMIS_* environment variables. uvicorn target: artemis.core.main:app

ARTEMIS_TOKEN      required; infra/start.ps1 generates it (contracts.md §6)
ARTEMIS_AGENT      stub (default) | claude
ARTEMIS_VOICE      whisper (default when the voice group is installed) | stub
ARTEMIS_RUNNER     real (default) | fake (writes files, never runs anything)
ARTEMIS_CONVERSATION  off (default) | on: built-in spoken phrases and follow-ups (contracts.md §5.4)
ARTEMIS_PROJECTS_DIR, ARTEMIS_LOG_DIR
"""

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


def agent_factory():
    try:
        from artemis.agent import make_agent  # PR #11; one agent per session

        return make_agent
    except ImportError:
        from artemis.agent.stub import StubAgent

        if os.environ.get("ARTEMIS_AGENT") == "claude":
            # Don't fall back silently (demo runbook review): say it in the core log at startup.
            log.warning("ARTEMIS_AGENT=claude, but this build has no real agent yet; using StubAgent")
        return StubAgent


def make_scan():
    try:
        from artemis.policy import scan  # PR #11 (Bandit)
    except ImportError:
        return None
    return scan.scan if scan.available() else None


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


def build():
    install_log_redaction()  # core.log gets tracebacks too; uvicorn has configured logging by now
    config = Config.from_env()
    log_dir = Path(os.environ.get("ARTEMIS_LOG_DIR") or Path.home() / ".artemis" / "logs")
    core = Core(
        voice=make_voice(),
        make_runner=runner_factory(),
        make_agent=agent_factory(),
        projects_dir=config.projects_dir,
        scan=make_scan(),
        log_dir=log_dir,
        vocabulary=make_vocabulary(),
    )
    return create_app(core, os.environ.get("ARTEMIS_TOKEN", ""))


app = build()
