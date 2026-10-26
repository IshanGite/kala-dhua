"""The knowledge graph for the Graph tab: a map of what Ragnarok holds, built from the real stores.

Real nodes: the eleven memory types (the realms), the local models, connected apps, people who wrote to you, the digest's topics, active facts, skills, skill scrolls and open reminders.
Everything here is read-only, local, and carries short labels only (a title or a name, never a message body).
The optional demo constellation adds clearly marked made-up nodes so a demo can show a big graph; it is never mixed in unless asked for, and every such node is flagged `demo`.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ragnarok.core.clock import get_clock

STAGES = [  # (id, label, what happens)
    ("intake", "Intake", "your message arrives; nothing is sent anywhere"), ("scan", "Scan", "received text is checked for instructions aimed at the assistant"),
    ("understand", "Understand", "what do you want: ask, act, catch up, look at the screen"), ("route", "Route", "learned route, rule, Laya, embedding, arbiter, planner: cheapest first"),
    ("plan", "Plan", "steps and their order; read steps feed write steps"), ("policy", "Policy", "rules decide the risk tier; models cannot grant permission"),
    ("approve", "Approve", "a card with the exact content waits for your click"), ("execute", "Execute", "the skill runs in the sandbox"),
    ("remember", "Remember", "memory is read or written, with a quote as evidence"), ("reply", "Reply", "the answer, with citations and a Why? trace"),
]
REALMS = [  # (id, label, norse flavour, table to count)
    ("working", "Working memory", "the task at hand", None), ("sensory", "Sensory buffer", "what just arrived", "items"), ("episodic", "Episodic", "Muninn's saga-log", "events"),
    ("semantic", "Semantic", "Mimir's well", "facts"), ("preference", "Preferences", "what you like", None), ("relational", "Relational", "who is who", "people"),
    ("prospective", "Prospective", "the Norns' threads", "commitments"), ("procedural", "Procedural", "the dwarves' forge", None), ("meta", "Meta-memory", "the seer's telling", None),
    ("project", "Project context", "your halls", None), ("saga", "Timeline", "the long saga", "saga"),
]
SRC_LABEL = {"gmail": "Gmail", "slack": "Slack", "discord": "Discord", "telegram": "Telegram", "gcal": "Calendar", "gdrive": "Drive", "gmeet": "Meet", "files": "Files", "notifications": "Notifications", "outlook": "Outlook", "outlook_cal": "Outlook calendar"}
DEMO_PEOPLE = ["Asha", "Ravi", "Meera", "Kabir", "Nina", "Dev", "Priya", "Tushar", "Zed", "Ingrid", "Bjorn", "Freya", "Sigrid", "Leif", "Astrid", "Ragnar", "Helga", "Ulf", "Thora", "Erik"]
DEMO_TOPICS = ["Offsite planning", "Hackathon demo", "Database migration", "Invoice 7731", "Design review", "Quarterly numbers", "Travel to Pune", "Contract renewal", "Release notes", "Hiring loop", "Customer feedback", "Security audit"]
DEMO_FACTS = ["prefers morning meetings", "uses dark mode", "team lead is Asha", "demo laptop is the M3", "birthday in March", "likes concise replies", "works in IST", "vegetarian", "rents in Pune", "uses Gmail for work"]


def _short(s: str, n: int = 38) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def build_graph(svc: Any, *, demo: bool = False, hours: float = 24 * 30) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []

    def node(nid: str, label: str, typ: str, size: float = 1.0, detail: str = "", **extra: Any) -> None:
        if nid not in nodes:
            nodes[nid] = {"id": nid, "label": _short(label), "type": typ, "size": round(size, 2), "detail": detail[:220], **extra}

    def edge(a: str, b: str, kind: str = "link") -> None:
        if a in nodes and b in nodes and a != b:
            edges.append({"s": a, "t": b, "k": kind})

    reader = svc.reader
    counts: dict[str, int] = {}
    for _id, _label, _flav, table in REALMS:
        if table and reader is not None:
            try:
                counts[_id] = reader.count(table)
            except Exception:  # noqa: BLE001
                counts[_id] = 0
    node("root", "Ragnarok", "root", 4.0, "A sovereign assistant: every model, memory and skill on this computer")
    for rid, label, flav, _t in REALMS:
        c = counts.get(rid)
        node(f"realm:{rid}", label, "realm", 1.6 + (math.log10(c + 1) * 0.5 if c else 0), f"{flav}" + (f" - {c} stored" if c is not None else ""))
        edge("root", f"realm:{rid}", "realm")

    # the pipeline every request goes through: a ring of stages, so a live run can be shown as a path through it
    prev = None
    for sid, label, tip in STAGES:
        node(f"stage:{sid}", label, "stage", 1.25, tip, fixed=True)
        edge(prev or "root", f"stage:{sid}", "flow")
        prev = f"stage:{sid}"
    edge("stage:reply", "root", "flow")
    # the local models (names only)
    try:
        lock = json.loads((Path(__file__).resolve().parents[3] / "models.lock").read_text(encoding="utf-8"))
        for role, v in lock.items():
            node(f"model:{role}", v["name"], "model", 1.3, f"{role} model, runs on this computer through Ollama")
            edge("root", f"model:{role}", "runs")
    except Exception:  # noqa: BLE001
        pass
    node("model:laya", "Laya router", "model", 1.2, "small in-process classifier that picks the fast path")
    edge("root", "model:laya", "runs")
    for sid, other in (("route", "model:laya"), ("understand", "model:arbiter"), ("plan", "model:planner"), ("execute", "realm:procedural"), ("remember", "realm:semantic"), ("scan", "realm:meta"), ("reply", "model:writer")):
        edge(f"stage:{sid}", other, "uses")

    # apps, people and topics from what was really read
    people: Counter[str] = Counter()
    person_srcs: dict[str, set[str]] = defaultdict(set)
    src_count: Counter[str] = Counter()
    cid = reader.compartment_id("work") if reader is not None else None
    items: list[dict[str, Any]] = []
    if reader is not None and cid:
        try:
            items = reader.items_window(cid, get_clock().now_ms() - int(hours * 3_600_000), limit=400)
        except Exception:  # noqa: BLE001
            items = []
    for it in items:
        src_count[it["source"]] += 1
        who = (it.get("author") or "").split("<")[0].strip()
        if who and it["source"] not in ("notifications", "gcal", "gdrive"):
            people[who] += 1
            person_srcs[who].add(it["source"])
    for s, n in src_count.items():
        node(f"src:{s}", SRC_LABEL.get(s, s), "app", 1.4 + math.log10(n + 1) * 0.5, f"{n} item(s) read from {SRC_LABEL.get(s, s)} in the last {int(hours / 24)} days")
        edge("root", f"src:{s}", "reads")
        edge(f"src:{s}", "realm:sensory", "feeds")
    for p, n in people.most_common(24):
        node(f"person:{p.lower()}", p, "person", 1.0 + math.log10(n + 1) * 0.6, f"{n} message(s) from {p}")
        edge("realm:relational", f"person:{p.lower()}", "knows")
        for s in person_srcs[p]:
            edge(f"person:{p.lower()}", f"src:{s}", "wrote")
    try:
        sections = svc.digest.build(hours)["sections"][:14] if svc.digest is not None else []
    except Exception:  # noqa: BLE001
        sections = []
    for i, sec in enumerate(sections):
        tid = f"topic:{i}"
        node(tid, sec["title"], "topic", 1.2 + math.log10(sec["count"] + 1) * 0.5, f"{sec['count']} item(s) about this" + (" - needs you" if sec.get("needs_action") else ""), alert=bool(sec.get("needs_action")))
        edge("realm:episodic", tid, "about")
        for s in sec["sources"]:
            edge(tid, f"src:{s}", "from")
        for a in {(x["author"] or "").split("<")[0].strip().lower() for x in sec["items"]}:
            edge(tid, f"person:{a}", "involves")

    # facts, skills, scrolls, reminders
    if reader is not None and cid:
        try:
            for f in reader.facts(cid)[:36]:
                hub = "realm:preference" if f["kind"] == "preference" else "realm:semantic"
                node(f"fact:{f['id'][:10]}", f"{f['subject']} {f['predicate']}", "fact", 0.9 + float(f.get("importance") or 0.5) * 0.5, f"{f['kind']}: {_short(f['value'], 90)}")
                edge(hub, f"fact:{f['id'][:10]}", "holds")
        except Exception:  # noqa: BLE001
            pass
    try:
        for s in (svc.skills.list() if svc.skills is not None else [])[:40]:
            node(f"skill:{s['name']}", s["name"], "skill", 1.0 + (0.3 if s.get("enabled") else 0), f"{s['description'][:100]} (tier {s.get('tier')})", off=not s.get("enabled"))
            edge("realm:procedural", f"skill:{s['name']}", "can")
    except Exception:  # noqa: BLE001
        pass
    try:
        for s in (svc.scrolls.lib.list() if svc.scrolls is not None else []):
            if s["status"] == "approved":
                node(f"scroll:{s['name']}", s["name"], "scroll", 1.1, _short(s["description"], 100))
                edge("realm:procedural", f"scroll:{s['name']}", "knows")
                for nxt in s.get("chain") or []:
                    edge(f"scroll:{s['name']}", f"scroll:{nxt}", "then")
    except Exception:  # noqa: BLE001
        pass
    try:
        for r in (svc.runtime.commitments.list_open() if svc.runtime and svc.runtime.commitments else [])[:10]:
            node(f"rem:{r['id']}", r["what"], "reminder", 1.0, "an open reminder")
            edge("realm:prospective", f"rem:{r['id']}", "due")
    except Exception:  # noqa: BLE001
        pass

    if demo:
        _constellation(nodes, edges)
    # a stable layout seed: the same graph starts in the same place
    for n in nodes.values():
        h = int(hashlib.sha1(n["id"].encode()).hexdigest()[:8], 16)
        n["seed"] = h / 0xFFFFFFFF
    deg: Counter[str] = Counter()
    for e in edges:
        deg[e["s"]] += 1
        deg[e["t"]] += 1
    for n in nodes.values():
        n["degree"] = deg[n["id"]]
    return {"nodes": list(nodes.values()), "edges": edges, "demo": demo, "counts": dict(Counter(n["type"] for n in nodes.values()))}


def _constellation(nodes: dict[str, dict[str, Any]], edges: list[dict[str, str]]) -> None:
    """Made-up nodes, flagged demo=True, so a demo can show a large graph. Seeded, so it looks the same every time."""
    rng = random.Random(1017)

    def add(nid: str, label: str, typ: str, size: float, detail: str) -> None:
        nodes.setdefault(nid, {"id": nid, "label": label, "type": typ, "size": size, "detail": detail + " (demo)", "demo": True})

    srcs = [k for k in nodes if k.startswith("src:")] or []
    for s in ("gmail", "slack", "telegram", "discord", "gcal", "gdrive"):
        if f"src:{s}" not in nodes:
            add(f"src:{s}", SRC_LABEL[s], "app", 1.6, "a connected app")
            edges.append({"s": "root", "t": f"src:{s}", "k": "reads"})
        srcs.append(f"src:{s}")
    people = []
    for p in DEMO_PEOPLE:
        pid = f"person:{p.lower()}"
        if pid in nodes:
            continue
        add(pid, p, "person", rng.uniform(.9, 1.7), f"{p} writes to you")
        edges.append({"s": "realm:relational", "t": pid, "k": "knows"})
        for s in rng.sample(srcs, rng.randint(1, 3)):
            edges.append({"s": pid, "t": s, "k": "wrote"})
        people.append(pid)
    for i, t in enumerate(DEMO_TOPICS):
        tid = f"topic:demo{i}"
        add(tid, t, "topic", rng.uniform(1.2, 2.0), "a thread across apps")
        edges.append({"s": "realm:episodic", "t": tid, "k": "about"})
        for p in rng.sample(people or ["root"], min(3, len(people) or 1)):
            edges.append({"s": tid, "t": p, "k": "involves"})
        for s in rng.sample(srcs, 2):
            edges.append({"s": tid, "t": s, "k": "from"})
    for i, f in enumerate(DEMO_FACTS):
        fid = f"fact:demo{i}"
        add(fid, f, "fact", rng.uniform(.9, 1.3), "a remembered fact")
        edges.append({"s": "realm:preference" if i % 3 == 0 else "realm:semantic", "t": fid, "k": "holds"})
        if people:
            edges.append({"s": fid, "t": rng.choice(people), "k": "about"})
    for i in range(30):  # memories hanging off the realms, so the whole tree looks alive
        rid = f"realm:{rng.choice([r[0] for r in REALMS])}"
        mid = f"mem:demo{i}"
        add(mid, f"memory {i + 1}", "fact", rng.uniform(.6, 1.0), "a stored memory")
        edges.append({"s": rid, "t": mid, "k": "holds"})
        if rng.random() < .5 and people:
            edges.append({"s": mid, "t": rng.choice(people), "k": "about"})


EVAL_TITLES = {"utility": "Exact answers", "intent": "Understanding", "actions": "Reading actions", "tasks": "Tasks end to end", "memory": "Grounded memory", "digest": "Digest", "forgetting": "Forgetting (180 days)",
               "security": "Red team", "reliability": "Reliability", "multimodal": "Pictures and speech"}


def eval_summary() -> dict[str, Any]:
    """The benchmark results saved by `ragnarok evals`, reshaped for the Benchmarks panel: rates with their intervals, latencies, and plain values."""
    d = Path(__file__).resolve().parents[3] / "docs" / "evidence" / "evals"
    out = []
    for name in EVAL_TITLES:
        f = d / f"{name}.json"
        if not f.exists():
            continue
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        rows = []
        for k, v in (r.get("metrics") or {}).items():
            if isinstance(v, dict) and {"k", "n", "ci95"} <= v.keys():
                rows.append({"name": k.replace("_", " "), "kind": "rate", "k": v["k"], "n": v["n"], "rate": v.get("rate"), "ci": v["ci95"]})
            elif isinstance(v, dict) and "p50_ms" in v:
                rows.append({"name": k.replace("_", " "), "kind": "latency", "p50": v["p50_ms"], "p95": v["p95_ms"], "n": v["n"]})
            elif isinstance(v, (int, float)):
                rows.append({"name": k.replace("_", " "), "kind": "value", "value": v})
        out.append({"suite": name, "title": EVAL_TITLES[name], "requirement": r.get("requirement", ""), "n": r.get("n"), "seconds": r.get("seconds"), "rows": rows[:14], "notes": (r.get("notes") or r.get("note") or "")[:260],
                    "misses": len(r.get("failures") or [])})
    return {"suites": out}
