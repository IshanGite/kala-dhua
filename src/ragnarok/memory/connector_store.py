"""Connector cursors and sender pairing (SQL lives under memory/ by rule)."""

from __future__ import annotations

import json
from typing import Any

from ragnarok.memory.service import MemoryService


class ConnectorStore:
    def __init__(self, mem: MemoryService, name: str) -> None:
        self._m, self._name = mem, name
        self._state_id = f"conn:{name}"

    def load_cursor(self) -> dict[str, Any] | None:
        row = self._m.with_tx(lambda c: c.execute("SELECT cursor_json FROM trigger_state WHERE id=?", (self._state_id,)).fetchone())
        return json.loads(row[0]) if row and row[0] != "{}" else None

    def save_cursor(self, value: dict[str, Any]) -> None:
        self._m.with_tx(lambda c: c.execute(
            "INSERT INTO trigger_state(id,kind,config_json,cursor_json) VALUES (?, 'connector', '{}', ?)"
            " ON CONFLICT(id) DO UPDATE SET cursor_json=excluded.cursor_json", (self._state_id, json.dumps(value))))

    def pairing(self, handle: str) -> str:
        """State of a sender; an unknown one is recorded as held and waits for the user."""
        def op(c: Any) -> str:
            r = c.execute("SELECT state FROM sender_pairing WHERE platform=? AND handle=?", (self._name, handle)).fetchone()
            if r is None:
                c.execute("INSERT INTO sender_pairing(platform,handle,state) VALUES (?,?, 'held')", (self._name, handle))
                return "held"
            return str(r[0])

        return str(self._m.with_tx(op))

    def set_pairing(self, handle: str, state: str) -> None:
        self._m.with_tx(lambda c: c.execute(
            "INSERT INTO sender_pairing(platform,handle,state) VALUES (?,?,?) ON CONFLICT(platform,handle) DO UPDATE SET state=excluded.state",
            (self._name, handle, state)))
