"""Latency of the generation tasks that must be fast: grounded answer (JSON, 3 snippets), mail draft fields, one-line chat. Per model: p50/p95 over 6 runs, tokens out, and sample outputs."""
import json, statistics, sys, time, urllib.request

SNIP = ('[1] source=discord:broseph #general from=Ishan Gite at=Sun 25 Oct 16:54\n<<<\nare we still on for the meet on monday\n>>>\n'
        '[2] source=gmail:[Ragnarok test] Orion launch update from=Priya Nair at=Sun 25 Oct 15:02\n<<<\nthe Orion launch has moved to 14 April because the vendor delayed the hardware. The budget review is on Thursday at 3pm in Room 4.\n>>>\n'
        '[3] source=gdrive:Orion plan at=Sun 25 Oct 16:40\n<<<\nOrion launch is 14 April. The phase two budget is 1.2 crore. Owner: Priya.\n>>>')
CASES = {
    "answer": ([{"role": "system", "content": "You answer questions about the user's own data. Use ONLY the numbered snippets. State WHO said it and WHEN when relevant. Reply as JSON {answer, cites}."},
                {"role": "user", "content": "Snippets:\n" + SNIP + "\n\nQuestion: who asked about the meet on monday and when"}],
               {"type": "object", "properties": {"answer": {"type": "string"}, "cites": {"type": "array", "items": {"type": "integer"}}}, "required": ["answer", "cites"]}, 80),
    "mail_fields": ([{"role": "system", "content": "Write a short email. Reply as JSON {subject, body}. Use only the facts given."},
                     {"role": "user", "content": "Write to Priya about invoice 4471 which is due on 12 November. Polite, two sentences."}],
                    {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["subject", "body"]}, 90),
    "chat": ([{"role": "system", "content": "You are a small local assistant. Answer in one short sentence. You cannot take actions."}, {"role": "user", "content": "what is the capital of france"}], None, 30),
}


def call(model, msgs, schema, n):
    body = {"model": model, "stream": False, "think": False, "keep_alive": "24h", "messages": msgs, "options": {"num_ctx": 2048, "num_predict": n, "temperature": 0, "seed": 0}}
    if schema:
        body["format"] = schema
    t = time.perf_counter()
    r = json.load(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:11434/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=120))
    return (time.perf_counter() - t) * 1000, r.get("eval_count", 0), r["message"]["content"]


for model in sys.argv[1:]:
    call(model, *CASES["chat"][:2], 5)  # load
    for name, (m, s, n) in CASES.items():
        ts, last = [], ""
        for _ in range(6):
            ms, toks, out = call(model, m, s, n)
            ts.append(ms); last = (toks, out)
        ts.sort()
        print(f"{model:12} {name:12} p50={statistics.median(ts):5.0f}ms p95={ts[-1]:5.0f}ms tokens={last[0]:3}  -> {last[1][:150]!r}")
