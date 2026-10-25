"""Pending approval cards: the executor waits here for a human click. Only the API (after the session-token check) may resolve one."""

from __future__ import annotations

import asyncio
from typing import Any

from ragnarok.broker.approvals import APPROVAL_TTL_MS, ApprovalError, Broker, UiSession


class ApprovalHub:
    def __init__(self, broker: Broker) -> None:
        self._b = broker
        self._waiting: dict[str, asyncio.Future[str | None]] = {}

    async def hook(self, approval_id: str, card: dict[str, Any]) -> str | None:
        fut: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        self._waiting[approval_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=APPROVAL_TTL_MS / 1000)
        except TimeoutError:
            return None
        finally:
            self._waiting.pop(approval_id, None)

    def pending(self) -> list[dict[str, Any]]:
        out = []
        for aid in list(self._waiting):
            try:
                out.append(self._b.card(aid))
            except ApprovalError:
                continue
        return out

    def decide(self, approval_id: str, approve: bool, *, typed_phrase: str | None = None) -> bool:
        fut = self._waiting.get(approval_id)
        if fut is None or fut.done():
            return False
        ui = UiSession(human=True, csrf_ok=True)  # constructed only by the API after the token check
        if approve:
            fut.set_result(self._b.approve(approval_id, ui, typed_phrase=typed_phrase))
        else:
            self._b.deny(approval_id, ui)
            fut.set_result(None)
        return True
