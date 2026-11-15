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
# How many times in one task a reply that hit the output limit is answered with "split it up".
MAX_CUT_OFFS = 2
CUT_OFF_NOTE = (
    "Your last reply hit the output limit and was cut off, so this tool call did NOT run and nothing "
    "was written. Split the work: put each file in its own write_files call, keep each file under "
    "about 300 lines (move parts into more files, such as separate css and js files), and send the "
    "smaller pieces one after another."
)
# Lets the API re-run a request that a safety classifier declined on the model it recommends
# for that kind of refusal, inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

log = logging.getLogger("artemis.agent")


def make_client(config: Config):
    """The SDK client. Build it once at startup, not per session: the SDK reads ANTHROPIC_API_KEY
    here, and a session's first call can overlap a Playwright driver start that hides it from os.environ.
    The constructor doesn't check the key, so a missing key still fails as a spoken error on the first call."""
    workspace = config.workspace_id
    headers = {"anthropic-workspace-id": workspace} if workspace else None
    return anthropic.AsyncAnthropic(default_headers=headers)


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
            # Not said here: the core speaks every Failed once, at high priority ("The task
            # failed. <message> You can try again."). Saying it here too put it in the log twice.
            return Failed(code=code, message=message)

        last_failed = False  # the last tool result was an error
        cut_offs = 0
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
            except anthropic.APITimeoutError:
                # A subclass of APIConnectionError, so it must come first: a slow answer is not "no internet".
                return await fail("timeout", "Claude took too long to answer. Try again.")
            except anthropic.APIConnectionError:
                return await fail("offline", "No internet connection to Claude.")
            except anthropic.APIStatusError as exc:
                log.error("Claude API error %s: %s", exc.status_code, redact(str(exc.message)))
                return await fail("api_error", "Claude returned an error.")
            except anthropic.AnthropicError as exc:
                # Raised before any request is sent when no credentials can be found.
                log.error("Claude client error: %s", redact(str(exc)))
                return await fail("auth", "The Claude API key is missing or invalid.")

            # Check the stop reason before reading content: a refusal has none worth reading.
            if response.stop_reason == "refusal":
                return await fail("refused", "I can't help with that request.")
            if response.stop_reason == "max_tokens":
                cut_offs += 1
                # A big file can run past the output limit. Never run a cut-off tool call (its input is
                # incomplete) and never hide it: say so, then let the model redo it in smaller pieces.
                if cut_offs > MAX_CUT_OFFS or not response.content:
                    return await fail(
                        "too_long",
                        "That was too big to write in one go, even in pieces. Try a smaller request.",
                    )
                await say(emit, task.task_id, "That was too big for one step. Splitting it into smaller files.", "progress")
                self.messages.append({"role": "assistant", "content": response.content})
                calls = [block for block in response.content if block.type == "tool_use"]
                if calls:
                    note = [
                        {"type": "tool_result", "tool_use_id": c.id, "content": CUT_OFF_NOTE, "is_error": True}
                        for c in calls
                    ]
                else:
                    note = "Your last reply hit the output limit and was cut off. Continue in smaller pieces."
                self.messages.append({"role": "user", "content": note})
                last_failed = bool(calls)
                continue

            # Exactly as returned, thinking blocks included.
            self.messages.append({"role": "assistant", "content": response.content})
            calls = [block for block in response.content if block.type == "tool_use"]
            if not calls:
                text = "\n".join(b.text for b in response.content if b.type == "text").strip()
                # An empty answer after a failed tool must not claim it worked.
                text = text or ("That did not work. Ask me to try again." if last_failed else "Done.")
                await say(emit, task.task_id, text)
                return Answered(text=text)

            # A sentence written alongside the tool calls ("I'll check the file first.") is what Claude
            # is doing right now: say it as progress, so the work is explained while it happens.
            # Low priority: it never cuts or delays an approval or a result, and a newer line replaces it.
            between = "\n".join(b.text for b in response.content if b.type == "text").strip()
            if between:
                await say(emit, task.task_id, between, "progress")

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
            last_failed = bool(results) and results[-1]["is_error"]

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
