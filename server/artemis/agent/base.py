"""The agent interface (defined in contracts) and how an agent speaks."""

from typing import Literal

from artemis.contracts import Agent, AgentMessageData, Announce, Emit, EventDraft

from .speech_text import for_speech, spoken_answer

__all__ = ["Agent", "Emit", "say"]


async def say(
    emit: Emit, task_id: str, text: str, kind: Literal["answer", "question", "progress"] = "answer"
) -> None:
    """Emit an agent.message. The spoken text has markdown stripped, and an answer or question is
    shaped for the ear (spoken_answer); the log keeps the original."""
    spoken = for_speech(text) if kind == "progress" else spoken_answer(text)
    if not spoken and kind != "progress":
        spoken = "Done."  # nothing but markers or emoji: never announce an empty line
    await emit(
        EventDraft(
            task_id=task_id,
            type="agent.message",
            announce=Announce(text=spoken, priority="low" if kind == "progress" else "normal"),
            data=AgentMessageData(text=text, kind=kind).model_dump(),
        )
    )
