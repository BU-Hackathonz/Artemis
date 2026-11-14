"""ClaudeAgent: the tool loop on Claude (design A1 to A9).

A manual, non-streaming loop over one append-only conversation per session. Thinking blocks
are only valid when sent back unchanged in the conversation that produced them, so nothing
that has been answered is ever edited or removed.
"""

import asyncio
import logging
from typing import Any

import anthropic

from artemis.contracts import AgentOutcome, Answered, Emit, Failed, TaskContext, ToolGateway
from artemis.policy.redact import redact

from .base import say
from .config import Config
from .context import task_message
from .prompts import system_prompt, tool_definitions

MAX_TOKENS = 16000
RETRY_DELAY_S = 2.0
# Lets the API re-run a request that a safety classifier declined on the model it recommends
# for that kind of refusal, inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

log = logging.getLogger("artemis.agent")


class ClaudeAgent:
    """One instance per session: it keeps the conversation across tasks."""

    def __init__(self, config: Config | None = None, client: Any = None):
        self._config = config or Config.from_env()
        self._client = client  # created on first use, so a missing key fails as a spoken error
        self._tools = tool_definitions()
        self._system = system_prompt(self._config.conversation)
        self.messages: list[dict] = []

    def reset(self) -> None:
        """Start over: forget the conversation."""
        self.messages = []

    async def run(self, task: TaskContext, tools: ToolGateway, emit: Emit) -> AgentOutcome:
        start = len(self.messages)
        self.messages.append({"role": "user", "content": task_message(task)})

        async def fail(code: str, message: str) -> Failed:
            # If Claude never answered this task, take its request back out so the next task
            # doesn't replay it. Nothing that was answered is ever removed.
            if len(self.messages) == start + 1:
                del self.messages[start:]
            await say(emit, task.task_id, message)
            return Failed(code=code, message=message)

        for _ in range(self._config.max_iterations):
            try:
                response = await self._create(task, emit)
            except asyncio.CancelledError:
                # Canceled while Claude was thinking. An unanswered request must not stay in
                # the conversation, or the next task would replay it along with its own.
                if len(self.messages) == start + 1:
                    del self.messages[start:]
                else:
                    self.messages.append(
                        {
                            "role": "user",
                            "content": "The developer canceled this task. Do not continue it.",
                        }
                    )
                raise
            except anthropic.AuthenticationError:
                return await fail("auth", "The Claude API key is missing or invalid.")
            except (anthropic.RateLimitError, anthropic.InternalServerError):
                return await fail("busy", "Claude is still busy. Try again in a minute.")
            except anthropic.APIConnectionError:
                return await fail("offline", "No internet connection to Claude.")
            except anthropic.APIStatusError as exc:
                log.error("Claude API error %s: %s", exc.status_code, redact(str(exc.message)))
                return await fail("api_error", "Claude returned an error. Try again.")
            except anthropic.AnthropicError as exc:
                # Raised before any request is sent when no credentials can be found.
                log.error("Claude client error: %s", redact(str(exc)))
                return await fail("auth", "The Claude API key is missing or invalid.")

            # Check the stop reason before reading content: a refusal has none worth reading.
            if response.stop_reason == "refusal":
                return await fail("refused", "I can't help with that request.")
            if response.stop_reason == "max_tokens":
                return await fail("too_long", "That got too long. Try a smaller request.")

            # Exactly as returned, thinking blocks included.
            self.messages.append({"role": "assistant", "content": response.content})
            calls = [block for block in response.content if block.type == "tool_use"]
            if not calls:
                text = "\n".join(b.text for b in response.content if b.type == "text").strip()
                text = text or "Done."
                await say(emit, task.task_id, text)
                return Answered(text=text)

            # The model may ask for several tools at once; run them one at a time, in order,
            # so there is never more than one approval open (design A7).
            results: list[dict] = []
            try:
                for call in calls:
                    args = call.input if isinstance(call.input, dict) else {}
                    result = await tools.call(task.task_id, call.name, args)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call.id,
                            "content": result.to_model_text(),
                            "is_error": not result.ok,
                        }
                    )
            except asyncio.CancelledError:
                # Every tool_use needs a result, or the conversation is unusable afterwards.
                for call in calls[len(results):]:
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call.id,
                            "content": '{"ok": false, "error": "canceled", "message": "The developer canceled the task."}',
                            "is_error": True,
                        }
                    )
                self.messages.append({"role": "user", "content": results})
                raise
            self.messages.append({"role": "user", "content": results})

        return await fail("too_many_steps", "That took too many steps. Try a smaller request.")

    async def _create(self, task: TaskContext, emit: Emit):
        """One model call. A rate limit or overload is spoken and retried once."""
        try:
            return await self._request()
        except (anthropic.RateLimitError, anthropic.InternalServerError):
            await say(emit, task.task_id, "Claude is busy, retrying.", "progress")
            await asyncio.sleep(RETRY_DELAY_S)
            return await self._request()

    async def _request(self):
        if self._client is None:
            # A key that isn't tied to one workspace needs the workspace named on every request.
            workspace = self._config.workspace_id
            headers = {"anthropic-workspace-id": workspace} if workspace else None
            self._client = anthropic.AsyncAnthropic(default_headers=headers)
        return await self._client.beta.messages.create(
            model=self._config.model,
            max_tokens=MAX_TOKENS,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            # Thinking is always on for this model, so no `thinking` parameter is sent.
            output_config={"effort": self._config.effort},
            cache_control={"type": "ephemeral"},
            system=self._system,
            tools=self._tools,
            messages=self.messages,
        )
