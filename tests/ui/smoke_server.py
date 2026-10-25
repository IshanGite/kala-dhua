"""A throwaway app (fake models, fake senders, seeded memory) that the browser-level UI smoke test drives. Prints one JSON line when ready, exits when stdin closes."""
import asyncio, json, os, sys, tempfile
os.environ["RAGNAROK_HOME"] = tempfile.mkdtemp()
os.environ["HOME"] = os.environ["RAGNAROK_HOME"]
sys.path.insert(0, "src")
from ragnarok.daemon.launch import make_api, start_in_thread  # noqa: E402
from ragnarok.daemon.wiring import build_world  # noqa: E402
from ragnarok.ingest.pipeline import ingest_text  # noqa: E402
from ragnarok.llm_gateway.fake import FakeGateway  # noqa: E402
from ragnarok.platform.secrets import MemoryStore, token_name  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8794


async def fake_cal(p, due):
    return "https://calendar.example/e"


def responder(messages, schema):
    props = (schema or {}).get("properties", {})
    if "answer" in props and "cites" in props:
        return {"answer": "The standup moved to ten, said Ishan on Discord.", "cites": [1]}
    if schema is None:
        return "Hello from the fake model."
    return {}


async def seed(w):
    await ingest_text(w.mem, w.gateway, w.index, source="discord", native_id="g:1", compartment="work", trust="received", text="The standup moved to ten tomorrow", title="broseph #general", extra_flags={"author": "Ishan"})
    await ingest_text(w.mem, w.gateway, w.index, source="gmail", native_id="m1", compartment="work", trust="received", text="Can you send me the signed contract by Friday?", title="Contract", extra_flags={"author": "Dev"}, deep_link="https://mail.google.com/mail/u/0/#all/m1")
    w.services.proactive.inbox.add("attention", "Needs your attention: Dev", "Contract: Can you send me the signed contract by Friday?", key="seed1", source="gmail")


vault = MemoryStore()
vault.set(token_name("google", "write"), json.dumps({"client_id": "a", "client_secret": "b", "refresh_token": "c"}))
w = build_world(vault=vault, gateway=FakeGateway(responder), sandbox=False, use_laya=False, embed_rungs=False, senders={"calendar": fake_cal}, max_cpu=101)
asyncio.run(seed(w))
server, _ = start_in_thread(make_api(w, PORT), PORT)
print(json.dumps({"port": PORT, "token": w.guard.token}), flush=True)
sys.stdin.read()
server.should_exit = True
w.mem.close()
