"""Desktop actions (contracts.md §9): the core asks the island to type, and waits for its answer.

Only the GNOME shell can type on Wayland, so after approval the gateway calls request(). It emits
desktop.action and waits; POST /desktop/{op_id}/result resolves the op. With no island connected
the answer is desktop_unavailable. The typed text is never logged here (op_id, kind and length only).
"""

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from typing import Literal

from artemis.contracts import DesktopActionData, DesktopResult, Event, EventDraft

log = logging.getLogger(__name__)

BASE_TIMEOUT_S = 10.0  # the island's answer is due after this plus PER_CHAR_S for every character
PER_CHAR_S = 0.05

UNAVAILABLE = DesktopResult(ok=False, message="desktop_unavailable")


class DesktopOps:
    def __init__(
        self,
        emit: Callable[[EventDraft], Awaitable[object]],
        *,
        ready: Callable[[], bool] = lambda: False,
        subscribers: Callable[[], int] = lambda: 0,
        forget: Callable[[str], None] = lambda op_id: None,
    ) -> None:
        self._emit = emit
        self._ready = ready  # a client called /ready with desktop=True
        self._subscribers = subscribers  # SSE subscribers connected right now
        self._forget = forget  # called with the op_id once the op is over, to drop its text from replay history
        self._pending: dict[str, asyncio.Future[DesktopResult]] = {}

    def available(self) -> bool:
        return bool(self._ready()) and self._subscribers() > 0

    def has(self, op_id: str) -> bool:
        return op_id in self._pending

    def is_stale(self, event: Event) -> bool:
        """A desktop.action whose op is already answered, expired or canceled. The event stream skips
        these, so an island that reconnects (or starts fresh and replays from 0) never types twice."""
        return event.type == "desktop.action" and not self.has(str(event.data.get("op_id")))

    async def request(
        self,
        kind: Literal["focus", "type", "keys"],
        text: str = "",
        task_id: str | None = None,
        window: str | None = None,
    ) -> DesktopResult:
        """focus: ask which window has the keyboard (the answer's `window`), before the approval.
        type / keys: type into `window`, the one the developer approved; the island refuses if focus moved."""
        if not self.available():
            return UNAVAILABLE.model_copy()
        op_id = "op_" + secrets.token_hex(12)
        future: asyncio.Future[DesktopResult] = asyncio.get_running_loop().create_future()
        self._pending[op_id] = future
        log.info("desktop op %s: %s, %d chars", op_id, kind, len(text))  # never the text itself
        try:
            await self._emit(
                EventDraft(
                    task_id=task_id,
                    type="desktop.action",
                    announce=None,
                    data=DesktopActionData(op_id=op_id, kind=kind, text=text, window=window).model_dump(),
                )
            )
            return await asyncio.wait_for(future, timeout=BASE_TIMEOUT_S + PER_CHAR_S * len(text))
        except asyncio.TimeoutError:
            # resolve() may have answered in the same loop turn the timer fired: the island did act, so
            # report its answer, not "unavailable" (the gate would otherwise treat typed text as untyped).
            if future.done() and not future.cancelled():
                return future.result()
            return UNAVAILABLE.model_copy()
        finally:
            self._pending.pop(op_id, None)  # decided, timed out or canceled: later results are ignored
            self._forget(op_id)

    def resolve(self, op_id: str, result: DesktopResult) -> bool:
        """Single use: False for an unknown, finished or expired op."""
        future = self._pending.pop(op_id, None)
        if future is None or future.done():
            return False
        future.set_result(result)
        return True

    def cancel_all(self) -> None:
        """Task canceled or session over: every waiting request returns ok=False, "canceled"."""
        self._finish_all(DesktopResult(ok=False, message="canceled"))

    def subscribers_changed(self) -> None:
        """An SSE stream closed. With nobody left to hear a desktop.action, the waiting requests end now
        (desktop_unavailable) instead of holding the task for the whole timeout."""
        if self._subscribers() <= 0:
            self._finish_all(UNAVAILABLE.model_copy())

    def _finish_all(self, result: DesktopResult) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_result(result.model_copy())
        self._pending.clear()
