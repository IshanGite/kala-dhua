import json
import os
import sys

req = json.load(sys.stdin)
P, CTX = req["params"], req["ctx"]
WS = os.path.realpath(CTX["workspace"])


def fail(code, retryable=False):
    print(json.dumps({"ok": False, "error": {"code": code, "retryable": retryable}}))
    sys.exit(0)


def inside(path):
    real = os.path.realpath(os.path.join(WS, path))
    if real != WS and not real.startswith(WS + os.sep):
        fail("E_PATH_OUTSIDE_WORKSPACE")
    return real

q = P["query"].lower()
cap = int(P.get("max_results", 50))
hits = []
for root, dirs, files in os.walk(WS):
    dirs[:] = [d for d in dirs if not d.startswith(".")]
    for name in files:
        if len(hits) >= cap:
            break
        full = os.path.join(root, name)
        try:
            if os.path.getsize(full) > 2_000_000:
                continue
            with open(full, encoding="utf-8", errors="strict") as fh:
                for n, line in enumerate(fh, 1):
                    if q in line.lower():
                        hits.append({"path": os.path.relpath(full, WS).replace(os.sep, "/"), "line": n, "text": line.strip()[:200]})
                        break
        except (OSError, UnicodeDecodeError):
            continue
print(json.dumps({"ok": True, "result": {"matches": hits, "count": len(hits)}}))
