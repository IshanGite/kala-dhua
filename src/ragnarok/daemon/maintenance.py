"""FR1.3 in the running app: consolidation and forgetting run on their own, once a day, off the request path. The first purge ever waits for the user."""

from __future__ import annotations

import json

from ragnarok.core.clock import get_clock
from ragnarok.core.logging import log
from ragnarok.daemon.proactive import Inbox
from ragnarok.memory.consolidate import consolidate
from ragnarok.memory.retention import Retention
from ragnarok.memory.runtime_state import RuntimeState
from ragnarok.memory.service import MemoryService

DAY_MS = 86_400_000


class Maintenance:
    def __init__(self, mem: MemoryService, state: RuntimeState, inbox: Inbox) -> None:
        self._m, self._s, self._inbox = mem, state, inbox
        self._ret = Retention(mem)

    def due(self) -> bool:
        return get_clock().now_ms() - int(self._s.get("maint_last", "0") or 0) >= DAY_MS

    def run(self, *, force: bool = False, allow_first_purge: bool = False) -> dict[str, int]:
        if not force and not self.due():
            return {}
        made = consolidate(self._m, older_than_days=14)
        rep = self._ret.run(allow_first_purge=allow_first_purge)
        self._s.set("maint_last", str(get_clock().now_ms()))
        out = {"summaries": made, "archived": rep.archived, "restored": rep.restored, "purged": rep.purged, "awaiting_first_purge": rep.awaiting_first_purge}
        self._s.set("maint_report", json.dumps(out))
        if rep.awaiting_first_purge:
            self._inbox.add("attention", f"{rep.awaiting_first_purge} old fact(s) are ready to be forgotten", "They were archived 30 days ago and never used. Say 'confirm forgetting' or open the Memory tab to review. Nothing is deleted until you agree.",
                            key=f"purge:{get_clock().now_ms() // DAY_MS}")
        log("maintenance_done", **{k: v for k, v in out.items()})
        return out

    def confirm_first_purge(self) -> dict[str, int]:
        return self.run(force=True, allow_first_purge=True)

    def last_report(self) -> dict[str, int]:
        try:
            return dict(json.loads(self._s.get("maint_report", "{}")))
        except ValueError:
            return {}
