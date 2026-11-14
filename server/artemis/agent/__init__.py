"""The agent: StubAgent for the walking skeleton, ClaudeAgent for the real thing."""

from artemis.contracts import Agent

from .config import Config


def make_agent(config: Config | None = None, client=None) -> Agent:
    """The one switch between agents: ARTEMIS_AGENT=stub (default) or claude.

    Create one per session; ClaudeAgent keeps the conversation across tasks. `client` is the SDK
    client built once at startup (agent.claude.make_client); without it, ClaudeAgent builds its own.
    """
    config = config or Config.from_env()
    if config.agent == "claude":
        from .claude import ClaudeAgent  # imported here so the stub works without the SDK

        return ClaudeAgent(config, client=client)
    from .stub import StubAgent

    return StubAgent()
