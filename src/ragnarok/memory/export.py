"""NFR4.2 export: everything the agent knows, with provenance, in JSON or Markdown. Read-only; the file is written by the caller, on the user's machine."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ragnarok.memory.reader import MemoryReader


def export_all(reader: MemoryReader, compartment: str = "work") -> dict[str, Any]:
    cid = reader.compartment_id(compartment)
    out: dict[str, Any] = {"exported_at": datetime.now(UTC).isoformat(), "compartment": compartment, "items": [], "facts": [], "events": []}
    if cid is None:
        return out
    for it in reader._r.execute("SELECT id, source, native_id, kind, title, trust, visibility, deep_link, occurred_at, ingested_at FROM items WHERE compartment_id=? ORDER BY coalesce(occurred_at, ingested_at)", (cid,)):
        d = dict(it)
        d["chunks"] = [r[0] for r in reader._r.execute("SELECT text FROM chunks WHERE item_id=? ORDER BY ordinal", (d["id"],))]
        out["items"].append(d)
    for f in reader.facts(cid, include_closed=True):
        f["provenance"] = [{"parent_type": p["parent_type"], "parent_id": p["parent_id"], "quote": p.get("quote")} for p in reader.provenance_of("fact", f["id"])]
        out["facts"].append(f)
    out["events"] = [dict(r) for r in reader._r.execute("SELECT ts, kind, actor, trust, content FROM events WHERE compartment_id=? ORDER BY seq LIMIT 5000", (cid,))]
    return out


def to_markdown(data: dict[str, Any]) -> str:
    lines = [f"# Ragnarok memory export ({data['compartment']})", f"Exported {data['exported_at']}", "", f"## Facts ({len(data['facts'])})"]
    for f in data["facts"]:
        lines.append(f"- **{f['subject']} / {f['predicate']}**: {f['value']}  _(state {f['state']}, trust {f['trust']}, {len(f['provenance'])} evidence)_")
    lines += ["", f"## Items ({len(data['items'])})"]
    for it in data["items"]:
        lines += [f"### {it['source']}: {it['title'] or it['kind']}", *[f"> {c[:600]}" for c in it["chunks"]], ""]
    lines += [f"## Conversation and event log ({len(data['events'])})"] + [f"- {e['kind']} ({e['actor']}): {str(e['content'])[:200]}" for e in data["events"][-200:]]
    return "\n".join(lines)


def dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=1, default=str)
