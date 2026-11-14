"""The agent: StubAgent for the walking skeleton, ClaudeAgent for the real thing."""

from artemis.contracts import Agent

from .config import Config


def make_agent(config: Config | None = None) -> Agent:
    """The one switch between agents: ARTEMIS_AGENT=stub (default) or claude.

    Create one per session; ClaudeAgent keeps the conversation across tasks.
    """
    config = config or Config.from_env()
    if config.agent == "claude":
        from .claude import ClaudeAgent  # imported here so the stub works without the SDK

        return ClaudeAgent(config)
    from .stub import StubAgent

    return StubAgent()
