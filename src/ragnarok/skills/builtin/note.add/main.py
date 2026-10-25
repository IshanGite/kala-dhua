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

path = os.path.join("notes", CTX["today"] + ".md")
target = inside(path)
if CTX.get("dry_run"):
    print(json.dumps({"ok": True, "result": {"would_append_to": path}}))
    sys.exit(0)
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "a", encoding="utf-8", newline="\n") as fh:
    fh.write("- " + P["text"].strip() + "\n")
print(json.dumps({"ok": True, "result": {"appended_to": path}}))
