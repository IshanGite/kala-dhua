"""Delete only what our own tests created in the user's real accounts: Gmail drafts to attacker@evil.example, calendar events titled in TEST_TITLES, Drive files named 'Ragnarok test ...'."""
import asyncio, json, sys
sys.path.insert(0, "src")
from ragnarok.connectors.google import Calendar, Gmail, GoogleAuth
from ragnarok.connectors.http import request
from ragnarok.platform.secrets import default_store, token_name

TEST_TITLES = {"hiring plan sync", "call with priya", "ragnarok test standup", "ragnarok test dentist", "ragnarok test orion review", "ragnarok test sync"}


def auth(kind):
    g = json.loads(default_store().get(token_name("google", kind)))
    return GoogleAuth(g["client_id"], g["client_secret"], g["refresh_token"])


async def main(dry):
    w, r = auth("write"), auth("read")
    out = {"drafts": 0, "events": 0, "files": 0}
    drafts = (await r.get(f"{Gmail.API}/drafts", maxResults=100)).json().get("drafts", [])
    for d in drafts:
        m = (await r.get(f"{Gmail.API}/drafts/{d['id']}", format="metadata", metadataHeaders="To")).json()
        to = " ".join(h["value"] for h in m["message"]["payload"]["headers"] if h["name"].lower() == "to").lower()
        if "attacker@evil.example" in to or "priya@acme.com" in to:
            out["drafts"] += 1
            if not dry:
                await request(w.http, "DELETE", f"{Gmail.API}/drafts/{d['id']}", headers={"Authorization": f"Bearer {await w.token()}"})
    ev = (await r.get(Calendar.API, singleEvents="true", maxResults=250, timeMin="2026-10-01T00:00:00Z")).json().get("items", [])
    for e in ev:
        if (e.get("summary") or "").strip().lower() in TEST_TITLES:
            out["events"] += 1
            if not dry:
                await request(w.http, "DELETE", f"{Calendar.API}/{e['id']}", headers={"Authorization": f"Bearer {await w.token()}"})
    files = (await r.get("https://www.googleapis.com/drive/v3/files", q="name contains 'Ragnarok test' and trashed = false", fields="files(id,name)")).json().get("files", [])
    for f in files:
        out["files"] += 1
        if not dry:
            await request(w.http, "DELETE", f"https://www.googleapis.com/drive/v3/files/{f['id']}", headers={"Authorization": f"Bearer {await w.token()}"})
    print(("would delete" if dry else "deleted"), out)

asyncio.run(main("--dry" in sys.argv))
