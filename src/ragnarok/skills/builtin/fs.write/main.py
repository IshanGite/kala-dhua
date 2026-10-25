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
exists = os.path.exists(target)
if exists and not P.get("overwrite", False):
    fail("E_EXISTS")
data = P["content"].encode("utf-8")
if CTX.get("dry_run"):
    print(json.dumps({"ok": True, "result": {"would_write": P["path"], "bytes": len(data), "would_overwrite": exists}}))
    sys.exit(0)
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "wb") as fh:
    fh.write(data)
print(json.dumps({"ok": True, "result": {"wrote": P["path"], "bytes": len(data), "overwrote": exists}}))
