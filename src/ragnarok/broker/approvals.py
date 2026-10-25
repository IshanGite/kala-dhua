"""Content-bound, single-use approvals (NFR4.3, SEC-02, SEC-20).

A click produces a token = HMAC(approval id | hash of the EXACT parameters | nonce | expiry). The broker verifies it again at
execution, so content cannot change after the click, a replayed token is refused, and a token for different parameters is
worthless. Only a human UI session may approve: the agent, skills and connectors never hold a UiSession.
"""

from __future__ import annotations

import hmac
import json
import os
from dataclasses import dataclass
from typing import Any

from ragnarok.broker.cards import render_card
from ragnarok.core.clock import get_clock
from ragnarok.core.errors import PolicyDenied
from ragnarok.core.ids import sha256_hex
from ragnarok.memory.ledger import Ledger
from ragnarok.store.crypto import KeyHierarchy

APPROVAL_TTL_MS = 10 * 60 * 1000
STEPUP_DELAY_MS = 10_000
UNDO_MS = 10_000


class ApprovalError(PolicyDenied):
    code = "E_APPROVAL"


@dataclass(frozen=True)
class UiSession:
    """Proof of a human at the UI: the daemon issues one only after the session token and CSRF check pass."""

    human: bool = False
    csrf_ok: bool = False


def canonical(params: dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def params_hash(params: dict[str, Any]) -> str:
    return sha256_hex(canonical(params))


class Broker:
    def __init__(self, ledger: Ledger, keys: KeyHierarchy) -> None:
        self._l, self._keys = ledger, keys

    def _now(self) -> int:
        return get_clock().now_ms()

    def _mac(self, approval_id: str, phash: str, nonce: str, expires: int) -> str:
        return self._keys.hmac("approval", f"{approval_id}|{phash}|{nonce}|{expires}".encode()).hex()

    def create_request(
        self, node_id: str, skill_id: str, params: dict[str, Any], *, tier: int, warnings: list[str], trust: str,
        dry_run: dict[str, Any] | None = None, snapshot_id: str | None = None, task_goal: str = "",
    ) -> str:
        now = self._now()
        stepup = None
        if tier >= 3:
            count = int(params.get("count", 1) or 1)
            stepup = {"phrase": f"CONFIRM {count}", "not_before": now + STEPUP_DELAY_MS}
        card = render_card(skill_id=skill_id, params=params, tier=tier, warnings=warnings, trust=trust, dry_run=dry_run,
                           snapshot_id=snapshot_id, stepup=stepup, task_goal=task_goal)
        return self._l.create_approval(node_id, skill_id, params_hash(params), card, warnings, now + APPROVAL_TTL_MS)

    def card(self, approval_id: str) -> dict[str, Any]:
        row = self._l.get_approval(approval_id)
        if row is None:
            raise ApprovalError("no such approval")
        return {**json.loads(row["card_json"]), "status": row["status"], "id": approval_id}

    def approve(self, approval_id: str, ui: UiSession, *, edit: dict[str, Any] | None = None, typed_phrase: str | None = None) -> str:
        if not (ui.human and ui.csrf_ok):
            raise ApprovalError("only a human UI session can approve", code="E_APPROVAL_NOT_HUMAN")
        row = self._l.get_approval(approval_id)
        if row is None:
            raise ApprovalError("no such approval")
        now = self._now()
        if row["status"] != "pending":
            raise ApprovalError(f"approval is {row['status']}", code="E_APPROVAL_STATE")
        if now > row["expires_at"]:
            self._l.update_approval(approval_id, status="expired")
            raise ApprovalError("approval expired", code="E_APPROVAL_EXPIRED")
        card = json.loads(row["card_json"])
        phash = row["params_hash"]
        if card.get("stepup"):
            if typed_phrase != card["stepup"]["phrase"] or now < card["stepup"]["not_before"]:
                raise ApprovalError("step-up phrase or delay not satisfied", code="E_STEPUP")
        if edit is not None:
            phash = params_hash(edit)
            card = render_card(skill_id=row["skill_id"], params=edit, tier=card["tier"], warnings=card["warnings"], trust=card["trust"],
                               dry_run=card.get("dry_run"), snapshot_id=card.get("snapshot_id"), stepup=card.get("stepup"), task_goal=card.get("task_goal", ""))
            card["edited"] = True
        nonce = os.urandom(16).hex()
        mac = self._mac(approval_id, phash, nonce, row["expires_at"])
        token = f"{approval_id}.{nonce}.{mac}"
        self._l.update_approval(approval_id, status="approved", params_hash=phash, card_json=json.dumps(card, sort_keys=True), decided_at=now,
                                token_hash=sha256_hex(token))
        return token

    def deny(self, approval_id: str, ui: UiSession) -> None:
        if not (ui.human and ui.csrf_ok):
            raise ApprovalError("only a human UI session can deny", code="E_APPROVAL_NOT_HUMAN")
        row = self._l.get_approval(approval_id)
        if row is not None and row["status"] == "pending":
            self._l.update_approval(approval_id, status="denied", decided_at=self._now())

    def verify_at_execution(self, token: str, params: dict[str, Any]) -> bool:
        """Re-verify at the moment of execution and consume the approval. False means: do not run."""
        try:
            approval_id, nonce, mac = token.split(".")
        except ValueError:
            return False
        row = self._l.get_approval(approval_id)
        if row is None or row["status"] != "approved" or self._now() > row["expires_at"]:
            return False
        phash = params_hash(params)
        if phash != row["params_hash"]:
            return False
        if not hmac.compare_digest(self._mac(approval_id, phash, nonce, row["expires_at"]), mac):
            return False
        return self._l.consume_approval_atomically(approval_id, sha256_hex(token))

    def expire_stale(self) -> int:
        now, n = self._now(), 0
        for row in self._l.pending_approvals():
            if now > row["expires_at"]:
                self._l.update_approval(row["id"], status="expired")
                n += 1
        return n

    @staticmethod
    def commit_release_at(now_ms: int) -> int:
        """External writes start after a short undo countdown the user can cancel (ARCHITECTURE 9)."""
        return now_ms + UNDO_MS
