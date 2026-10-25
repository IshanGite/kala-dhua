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

target = inside(P["path"])
if not os.path.isfile(target):
    fail("E_NOT_FOUND")
limit = int(P.get("max_chars", 20000))
with open(target, encoding="utf-8", errors="replace") as fh:
    text = fh.read(limit + 1)
print(json.dumps({"ok": True, "result": {"path": P["path"], "text": text[:limit], "truncated": len(text) > limit}}))
