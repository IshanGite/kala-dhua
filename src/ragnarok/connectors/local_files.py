"""Local folders: the connector every machine has. Polls by (mtime, size); parsing runs in the isolated parse worker."""

from __future__ import annotations

import asyncio
import fnmatch
from pathlib import Path

from ragnarok.connectors.base import ActionSpec, ConnectorInfo, Cursor, Health, ItemRef, RawItem, SyncResult
from ragnarok.ingest.parse import ParseError, parse_file

SUPPORTED = {".txt", ".md", ".markdown", ".csv", ".log", ".json", ".yaml", ".yml", ".rst", ".org", ".html", ".htm", ".eml", ".ics", ".pdf", ".docx"}
MAX_BYTES = 50 * 1024 * 1024


class LocalFiles:
    name = "files"

    def __init__(self, roots: list[Path], *, ignore: tuple[str, ...] = (".*", "node_modules", "__pycache__", "*.tmp", "~$*")) -> None:
        self._roots = [Path(r).expanduser().resolve() for r in roots]
        self._ignore = ignore

    def describe(self) -> ConnectorInfo:
        return ConnectorInfo(self.name, "Local files", ("file", "message", "event"), default_minimisation="named folders only; hidden and temp files skipped")

    def _skip(self, p: Path, root: Path) -> bool:
        return any(fnmatch.fnmatch(part, pat) for part in p.relative_to(root).parts for pat in self._ignore)

    def _scan(self, seen: dict[str, list[int]]) -> tuple[list[ItemRef], dict[str, list[int]]]:
        refs: list[ItemRef] = []
        now: dict[str, list[int]] = {}
        for root in self._roots:
            if not root.is_dir():
                continue
            for p in sorted(root.rglob("*")):
                try:
                    if not p.is_file() or p.suffix.lower() not in SUPPORTED or self._skip(p, root) or p.is_symlink():
                        continue
                    st = p.stat()
                except OSError:
                    continue
                if st.st_size > MAX_BYTES:
                    continue
                key = str(p)
                sig = [int(st.st_mtime_ns // 1_000_000), st.st_size]
                now[key] = sig
                if seen.get(key) != sig:
                    refs.append(ItemRef(key, {"version": f"{sig[0]}-{sig[1]}"}, priority=1 if seen else 5))
        return refs, now

    async def sync(self, cursor: Cursor | None) -> SyncResult:
        seen = (cursor.value if cursor else {}).get("seen", {})
        refs, now = await asyncio.to_thread(self._scan, seen)
        return SyncResult(refs, Cursor({"seen": now}))

    async def fetch(self, ref: ItemRef) -> RawItem | None:
        p = Path(ref.native_id)
        if not p.is_file():
            return None
        try:
            data = await asyncio.to_thread(parse_file, p)
        except ParseError:
            raise
        return RawItem(
            source=self.name, native_id=ref.native_id, text=data["text"], trust="user_file", title=data.get("title") or p.name,
            kind=data.get("kind", "file"), occurred_at=int(p.stat().st_mtime * 1000), html_hidden=bool(data.get("hidden")),
        )

    def actions(self) -> list[ActionSpec]:
        return []

    async def health(self) -> Health:
        missing = [str(r) for r in self._roots if not r.is_dir()]
        return Health(not missing, f"missing: {len(missing)}" if missing else f"{len(self._roots)} folders")
