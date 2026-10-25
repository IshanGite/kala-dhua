"""FR2.1 in the running app: list, register, enable, disable and remove skills. Enabling gives a skill permissions, so it needs a step-up approval for exactly this skill and version."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ragnarok.broker.approvals import Broker
from ragnarok.core.errors import RagnarokError
from ragnarok.core.logging import log
from ragnarok.daemon.approvals import ApprovalHub
from ragnarok.memory.ledger import Ledger
from ragnarok.router.select import SkillSelector
from ragnarok.skills.manifest import load_manifest
from ragnarok.skills.registry import SkillRegistry

BUILTIN_DIRS = ("builtin", "native")


class SkillsAdmin:
    def __init__(self, registry: SkillRegistry, ledger: Ledger, broker: Broker, hub: ApprovalHub, selector: SkillSelector, gateway: Any, builtin_names: set[str]) -> None:
        self._r, self._l, self._b, self._hub, self._sel, self._gw = registry, ledger, broker, hub, selector, gateway
        self._builtin = builtin_names
        self._indexed: list[str] = []

    def _manifest(self, name: str) -> Any | None:
        try:
            return load_manifest(self._r._path(name))
        except Exception:  # noqa: BLE001 - a damaged skill folder just does not show details
            return None

    def list(self) -> list[dict[str, Any]]:
        out = []
        for row in self._l.list_skills():
            m = self._manifest(row["skill_id"])
            scan = json.loads(row["scan_report"] or "{}")
            out.append({"name": row["skill_id"], "version": row["version"], "enabled": bool(row["enabled"]), "builtin": row["skill_id"] in self._builtin,
                        "description": (m.description if m else "")[:200], "tier": int(m.tier) if m else None, "permissions": m.permission_names if m else [],
                        "blocked": bool(scan.get("blocked")), "findings": len(scan.get("findings", [])) if isinstance(scan.get("findings"), list) else 0})
        return out

    async def reindex(self) -> None:
        """Skill shortlisting uses descriptions and examples: refresh them after any change."""
        from ragnarok.core.clock import get_clock

        manifests = []
        for row in self._l.list_skills():
            if row["enabled"] and (m := self._manifest(row["skill_id"])) is not None:
                manifests.append(m)
        names = [m.name for m in manifests]
        if names != self._indexed:
            try:
                await self._sel.index(manifests, get_clock().monotonic() + 60)
                self._indexed = names
            except Exception as exc:  # noqa: BLE001 - routing degrades to the planner
                log("skill_reindex_failed", err=type(exc).__name__)

    def register(self, folder: str) -> dict[str, Any]:
        p = Path(folder).expanduser().resolve()
        home = Path.home().resolve()
        if not (p == home or home in p.parents):
            raise RagnarokError("a skill folder must be inside your home folder", code="E_SKILL_PATH")
        if not (p / "manifest.json").is_file():
            raise RagnarokError("no manifest.json in that folder", code="E_SKILL_MANIFEST")
        reg = self._r.register(p)  # copies it into the data folder, scans it, hashes it; it stays DISABLED
        return {"name": reg.skill_id, "blocked": reg.report.blocked, "findings": reg.report.as_dict().get("findings", []), "enabled": False, "tier": int(reg.manifest.tier)}

    async def enable(self, name: str) -> dict[str, Any]:
        """Creates a step-up approval card and waits for the click in the background; the skill turns on only after exactly this card is approved."""
        params = self._r.enable_request_params(name)
        tid = self._l.create_task("work", f"enable skill {name}", "user", name[:12], plan={"nodes": []})  # an approval hangs off a task node, so admin actions get one
        nid = self._l.add_node(tid, 0, "skill.enable", params, tier=3)
        aid = self._b.create_request(nid, "skill.enable", params, tier=3, warnings=[f"Enabling '{name}' lets it run with the permissions it declares"], trust="user_direct", task_goal=f"enable skill {name}")

        async def wait() -> None:
            token = await self._hub.hook(aid, self._b.card(aid))
            if token is None:
                self._l.set_task_state(tid, "failed")  # the admin task ends here, so a restart does not try to resume it
                return
            try:
                self._r.enable(name, token)
                self._l.set_task_state(tid, "succeeded")
                await self.reindex()
            except Exception as exc:  # noqa: BLE001
                self._l.set_task_state(tid, "failed")
                log("skill_enable_failed", err=type(exc).__name__)

        asyncio.get_running_loop().create_task(wait())
        return {"ok": True, "approval_id": aid}

    async def disable(self, name: str) -> None:
        self._r.disable(name)
        await self.reindex()

    async def remove(self, name: str) -> None:
        if name in self._builtin:
            raise RagnarokError("built-in skills cannot be removed", code="E_SKILL_BUILTIN")
        self._r.disable(name)
        self._l.remove_skill(name)
        await self.reindex()
