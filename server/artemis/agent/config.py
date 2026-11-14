"""Settings from ARTEMIS_* environment variables (design E2).

ANTHROPIC_API_KEY is deliberately not here: only the SDK reads it, and it is never stored
on our objects. ANTHROPIC_WORKSPACE_ID is optional and is here because the SDK doesn't read
it: set it when the API answers that the key "is not scoped to a workspace".
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class Config:
    agent: Literal["stub", "claude"] = "stub"
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    projects_dir: Path = Path.home() / "ArtemisProjects"
    max_iterations: int = 12
    # Only for an API key that isn't scoped to a workspace: sent as the anthropic-workspace-id
    # header. Never logged or spoken.
    workspace_id: str | None = None

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ
        agent = env.get("ARTEMIS_AGENT", cls.agent)
        if agent not in ("stub", "claude"):
            raise ValueError(f"ARTEMIS_AGENT must be stub or claude, not {agent!r}")
        return cls(
            agent=agent,
            model=env.get("ARTEMIS_MODEL", cls.model),
            effort=env.get("ARTEMIS_EFFORT", cls.effort),
            projects_dir=Path(env.get("ARTEMIS_PROJECTS_DIR") or cls.projects_dir),
            max_iterations=int(env.get("ARTEMIS_MAX_ITERATIONS", cls.max_iterations)),
            workspace_id=env.get("ANTHROPIC_WORKSPACE_ID", "").strip() or None,
        )
