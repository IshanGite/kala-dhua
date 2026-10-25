"""Measure the embedding-similarity rung: how often is the shortlist's top skill right, by similarity and margin. Real embedder (nomic via Ollama). Output: docs/spikes/embed_rung.json."""
import asyncio, json, os, sys, tempfile, time
from pathlib import Path
os.environ["RAGNAROK_SECRETS"] = "memory"; os.environ["RAGNAROK_HOME"] = tempfile.mkdtemp(); sys.path.insert(0, "src")
from ragnarok.daemon.wiring import FAMILY_SKILLS, build_world, warm_up  # noqa: E402
from ragnarok.llm_gateway.client import Priority  # noqa: E402
from ragnarok.platform.secrets import MemoryStore  # noqa: E402

ACCEPT = {("mail", "none"): {"memory.search"}, ("mail", "low"): {"mail.draft"}, ("mail", "high"): {"mail.send", "mail.draft"}, ("chat", "none"): {"memory.search"}, ("chat", "high"): {"chat.reply"},
          ("notes", "low"): {"reminder.add"}, ("memory", "none"): {"memory.search"}, ("files", "none"): {"fs.search", "fs.read"}, ("code", "none"): {"python.run"}, ("code", "low"): {"python.run"}}
GENERAL = ["hello how are you", "what is the weather today", "who is the president of france", "tell me a joke", "what is the capital of japan", "thanks", "good morning", "what can you do",
           "how tall is mount everest", "who wrote hamlet", "can you explain what a black hole is", "sing me a song", "what year did world war two end", "is it going to rain tomorrow", "translate hello to spanish"]
async def main():
    w = build_world(vault=MemoryStore(), sandbox=False, max_cpu=101, use_laya=False)
    await warm_up(w)
    rows = [json.loads(x) for x in Path("evidence/router_synth.jsonl").read_text(encoding="utf-8").splitlines()]
    rows += [dict(text=t, family=f, injection=i, risk=r) for t, f, i, r in json.loads(Path("docs/spikes/laya_eval_set.json").read_text(encoding="utf-8"))]
    data = []
    for r in rows:
        ok = ACCEPT.get((r["family"], r["risk"]))
        if r.get("injection") == "yes" or ok is None or r.get("plan") == "yes":
            continue
        v = (await w.gateway.embed([r["text"]], deadline=time.monotonic() + 30, priority=Priority.INTERACTIVE))[0]
        top = w.selector.top(v, 3)
        data.append((top[0][1], top[0][1] - top[1][1], top[0][0] in ok, False))
    for q in GENERAL:
        v = (await w.gateway.embed([q], deadline=time.monotonic() + 30, priority=Priority.INTERACTIVE))[0]
        top = w.selector.top(v, 3)
        data.append((top[0][1], top[0][1] - top[1][1], False, True))
    out = {"n_tool": sum(1 for d in data if not d[3]), "n_general": sum(1 for d in data if d[3]), "grid": []}
    for hi in (0.55, 0.6, 0.65, 0.7):
        for mg in (0.0, 0.05, 0.1):
            acc = [d for d in data if d[0] >= hi and d[1] >= mg]
            tool_acc = [d for d in acc if not d[3]]
            out["grid"].append({"sim>=": hi, "margin>=": mg, "accepted_tool": len(tool_acc), "tool_correct": sum(d[2] for d in tool_acc), "general_wrongly_accepted": sum(1 for d in acc if d[3]),
                                "precision_on_tool": round(sum(d[2] for d in tool_acc) / len(tool_acc), 3) if tool_acc else None})
    for lo in (0.45, 0.5, 0.55):
        out.setdefault("general_below", {})[str(lo)] = {"general_caught": sum(1 for d in data if d[3] and d[0] < lo), "tool_wrongly_below": sum(1 for d in data if not d[3] and d[0] < lo)}
    Path("docs/spikes/embed_rung.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out))
    w.mem.close()
asyncio.run(main())
