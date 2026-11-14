"""The agent interface (defined in contracts) and how an agent speaks."""

from typing import Literal

from artemis.contracts import Agent, AgentMessageData, Announce, Emit, EventDraft

from .speech_text import for_speech

__all__ = ["Agent", "Emit", "say"]


async def say(
    emit: Emit, task_id: str, text: str, kind: Literal["answer", "question", "progress"] = "answer"
) -> None:
    """Emit an agent.message. The spoken text has markdown stripped; the log keeps the original."""
    await emit(
        EventDraft(
            task_id=task_id,
            type="agent.message",
            announce=Announce(text=for_speech(text), priority="low" if kind == "progress" else "normal"),
            data=AgentMessageData(text=text, kind=kind).model_dump(),
        )
    )
