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

import contextlib
import io

if CTX.get("dry_run"):
    print(json.dumps({"ok": True, "result": {"would_run_chars": len(P["code"])}}))
    sys.exit(0)
buf = io.StringIO()
try:
    with contextlib.redirect_stdout(buf):
        exec(compile(P["code"], "<skill>", "exec"), {"__name__": "__skill__"})
except Exception as exc:
    fail("E_CODE_" + type(exc).__name__.upper())
print(json.dumps({"ok": True, "result": {"stdout": buf.getvalue()[:20000]}}))
