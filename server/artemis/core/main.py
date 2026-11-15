"""Builds the core from ARTEMIS_* environment variables. uvicorn target: artemis.core.main:app

ARTEMIS_TOKEN      required; infra/start.ps1 generates it (contracts.md §6)
ARTEMIS_AGENT      stub (default) | claude
ARTEMIS_VOICE      whisper (default when the voice group is installed) | stub
ARTEMIS_RUNNER     real (default) | fake (writes files, never runs anything)
ARTEMIS_CONVERSATION  off (default) | on: built-in spoken phrases and follow-ups (contracts.md §5.4)
ARTEMIS_RESULT_EARCONS  off (default) | on: a success, failure or approval tone just before that line is
                        spoken, never while listening (contracts.md §5.5); session.py
ARTEMIS_WHISPER_MODEL   small.en (default) | medium.en (more accurate, slower); voice/listen.py
ARTEMIS_WHISPER_BEAM    1 (default) | 2 to 10, e.g. 5: opt-in
ARTEMIS_PTT_POST_ROLL   0 (default, off) | seconds up to 1.0, e.g. 0.35: opt-in
ARTEMIS_WHISPER_PROMPT  word list (default) | natural (spoken-style lead): opt-in
  Opt-in speech settings go on stage only after docs/demo.md row 3 passes with them on, on the demo laptop.
ARTEMIS_PROJECTS_DIR    ~/ArtemisProjects (default); where projects are created and opened
ARTEMIS_LOG_DIR         ~/.artemis/logs (default); one <session_id>.jsonl event log per session

Also read by the parts this builds, so the whole list is here:
ARTEMIS_MODEL           claude-opus-5-5 (default); agent/config.py, with ARTEMIS_EFFORT (medium)
                        and ARTEMIS_MAX_ITERATIONS (12)
ANTHROPIC_API_KEY       needed by ARTEMIS_AGENT=claude; read only by the Anthropic SDK
ANTHROPIC_WORKSPACE_ID  optional, for a key not scoped to a workspace; agent/config.py
ARTEMIS_RUN_PYTHON      the Python that runs user programs (default: uv python find, py -3, then
                        PATH; never the core's own); runner/local.py, contracts.md §3.4
ARTEMIS_SAPI_VOICE      part of an installed SAPI voice's name; voice/speech.py
ARTEMIS_SAPI_RATE       -3 to 6 (clamped); default the Windows rate; voice/speech.py, contracts.md §5.3
ARTEMIS_NVDA_DLL        path to nvdaControllerClient.dll (default voice/bin/); voice/speech.py
ARTEMIS_CHROME          a browser executable for Echo Check, tried first; echo/check.py
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
    )
    return create_app(core, os.environ.get("ARTEMIS_TOKEN", ""))


app = build()
