"""Approvals (contracts.md §3.2): the gateway awaits a decision; POST /approvals/{aid} resolves it.

The gateway emits approval.requested / approval.resolved and the task states itself. This only
holds the pending futures and checks that the decision is for the exact approval that was offered.
"""

import asyncio

from artemis.contracts import Approval, Decision


class ApprovalNotFound(Exception):
    pass


class ApprovalStale(Exception):
    """The hash doesn't match the one offered, or the approval was already decided."""


class SessionApprovals:
    def __init__(self) -> None:
        self._pending: dict[str, tuple[Approval, asyncio.Future[Decision]]] = {}
        self._seen: set[str] = set()  # every approval id ever offered in this session

    async def request(self, task_id: str, approval: Approval) -> Decision:
        future: asyncio.Future[Decision] = asyncio.get_running_loop().create_future()
        self._pending[approval.approval_id] = (approval, future)
        self._seen.add(approval.approval_id)
        try:
            return await future
        finally:
            # Canceled or decided: either way nobody can answer it any more.
            self._pending.pop(approval.approval_id, None)

    def has(self, approval_id: str) -> bool:
        return approval_id in self._seen

    def pending(self) -> list[Approval]:
        return [a for a, _ in self._pending.values()]

    def resolve(self, approval_id: str, decision: str, approval_hash: str) -> None:
        entry = self._pending.get(approval_id)
        if entry is None:
            if approval_id in self._seen:  # already decided, canceled or invalidated
                raise ApprovalStale(approval_id)
            raise ApprovalNotFound(approval_id)
        approval, future = entry
        if future.done() or approval_hash != approval.approval_hash:
            raise ApprovalStale(approval_id)
        future.set_result(Decision(decision))
