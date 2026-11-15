"""The event bus (contracts.md §2): one per session.

Components hand it an EventDraft; the bus adds seq, session_id and at, keeps the event for
Last-Event-ID replay, appends it to the session's JSONL log, fans it out to SSE subscribers and
speaks its `announce` through the voice. Nothing else in the core speaks.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from artemis.contracts import Event, EventDraft, Priority

log = logging.getLogger(__name__)

Speak = Callable[[str, Priority], Awaitable[None]]

MAX_HISTORY = 5000  # events kept for replay; a weekend session stays far below this


class EventBus:
    def __init__(self, session_id: str, speak: Speak | None = None, log_path: Path | None = None):
        self.session_id = session_id
        self._speak = speak
        self._log_path = log_path
        self._seq = 0
        self.history: list[Event] = []
        self._subscribers: set[asyncio.Queue[Event]] = set()

    @property
    def last_seq(self) -> int:
        return self._seq

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def emit(self, draft: EventDraft) -> Event:
        self._seq += 1
        event = Event(
            **draft.model_dump(),
            seq=self._seq,
            session_id=self.session_id,
            at=datetime.now(UTC),
        )
        self.history.append(event)
        if len(self.history) > MAX_HISTORY:
            del self.history[: len(self.history) - MAX_HISTORY]
        self._write_log(event)
        for queue in list(self._subscribers):
            queue.put_nowait(event)
        if event.announce and self._speak:
            try:
                await self._speak(event.announce.text, event.announce.priority)
            except Exception:  # speech failing must never stop the task or the stream
                log.exception("announce failed")
        return event

    __call__ = emit  # an EventBus is an `Emit`

    def _write_log(self, event: Event) -> None:
        if not self._log_path:
            return
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(self._log_line(event) + "\n")
        except OSError:
            log.exception("event log write failed")

    @staticmethod
    def _log_line(event: Event) -> str:
        """The JSONL line. Typed text never goes to disk: SSE carries it, the log only its length."""
        if event.type == "desktop.action":
            data = {**event.data, "text": f"<redacted {len(str(event.data.get('text', '')))} chars>"}
            event = event.model_copy(update={"data": data})
        elif event.type in ("approval.requested", "approval.resolved"):
            data, quoted, mark = _redact_typing(event.data)
            if data is not event.data:
                update: dict = {"data": data}
                if event.announce is not None:  # the spoken line quotes it too
                    said = event.announce.text
                    said = said.replace(quoted, mark) if quoted and quoted in said else "<redacted: typing approval>"
                    update["announce"] = event.announce.model_copy(update={"text": said})
                event = event.model_copy(update=update)
        return event.model_dump_json()

    def forget_desktop_text(self, op_id: str) -> None:
        """The op is over: drop its typed text from the replay history (the SSE copy was already sent)."""
        for i, e in enumerate(self.history):
            if e.type == "desktop.action" and e.data.get("op_id") == op_id and e.data.get("text"):
                self.history[i] = e.model_copy(update={"data": {**e.data, "text": ""}})

    def since(self, seq: int) -> list[Event]:
        return [e for e in self.history if e.seq > seq]

    def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(queue)


def _redact_typing(data: dict) -> tuple[dict, str, str]:
    """An approval to type text carries the text in desktop.text, in the dialog summary and in the spoken
    line; the log keeps none of them. Returns (data, the quoted text, its marker); nothing to hide: (data, "", "").
    When the summary doesn't quote the text the way we expect, the whole summary is redacted."""
    desktop = data.get("desktop")
    if not isinstance(desktop, dict) or desktop.get("action") not in ("type_text", "press_keys") or not desktop.get("text"):
        return data, "", ""
    text = str(desktop["text"])
    mark = f"<redacted {len(text)} chars>"
    quoted = " ".join(text.split())[:120].rstrip()  # the summary quotes it collapsed to one line, cut at 120
    summary = str(data.get("summary", ""))
    summary = summary.replace(quoted, mark) if quoted and quoted in summary else "<redacted: typing approval>"
    return {**data, "desktop": {**desktop, "text": mark}, "summary": summary}, quoted, mark
