"""FastAPI app for the local UI and CLI. Binds 127.0.0.1 only. Every route except /healthz needs the session token."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ragnarok import __version__
from ragnarok.connectors.notifications import Notifications
from ragnarok.daemon.approvals import ApprovalHub
from ragnarok.daemon.auth import LocalOnlyMiddleware, TokenGuard
from ragnarok.daemon.chat import ChatService
from ragnarok.daemon.runtime import Runtime
from ragnarok.harness.answer import Answerer
from ragnarok.memory.reader import MemoryReader

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


@dataclass
class Services:
    runtime: Runtime | None = None
    answerer: Answerer | None = None
    notifications: Notifications | None = None
    chat: ChatService | None = None
    reader: MemoryReader | None = None
    hub: ApprovalHub | None = None


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ToggleIn(BaseModel):
    enabled: bool


class NotifIn(BaseModel):
    apps: list[str] = Field(max_length=50)


class DecideIn(BaseModel):
    action: str = Field(pattern="^(approve|deny)$")
    phrase: str | None = Field(default=None, max_length=60)


class PairIn(BaseModel):
    platform: str = Field(max_length=40)
    handle: str = Field(max_length=200)
    action: str = Field(pattern="^(approve|block)$")


def create_app(guard: TokenGuard | None = None, port: int = DEFAULT_PORT, services: Services | None = None) -> FastAPI:
    guard = guard or TokenGuard()
    svc = services or Services()
    app = FastAPI(title="Ragnarok", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(LocalOnlyMiddleware, port=port, guard=guard, public_paths=frozenset({"/healthz", "/"}))
    app.state.guard, app.state.services = guard, svc

    ui_file = Path(__file__).resolve().parents[1] / "ui" / "index.html"

    @app.get("/", response_class=HTMLResponse)
    async def ui() -> str:
        return ui_file.read_text(encoding="utf-8")  # no secrets in the page; the session token arrives in the URL fragment from the launcher

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/status")
    async def status() -> dict[str, Any]:
        out: dict[str, Any] = {"ok": True, "version": __version__, "degraded": []}
        if svc.runtime:
            out["runtime"] = svc.runtime.status()
        return out

    @app.get("/activity")
    async def activity(limit: int = 50) -> dict[str, Any]:
        return {"items": list(svc.runtime.activity)[: max(1, min(limit, 200))] if svc.runtime else []}

    @app.post("/toggle")
    async def toggle(body: ToggleIn) -> dict[str, Any]:
        if svc.runtime is None:
            return {"ok": False, "error": "E_NO_RUNTIME"}
        svc.runtime.set_enabled(body.enabled)
        return {"ok": True, "enabled": svc.runtime.enabled}

    @app.post("/tick")
    async def tick() -> dict[str, Any]:
        """Run one sync cycle now (the window's Sync button)."""
        if svc.runtime is None:
            return {"ok": False, "error": "E_NO_RUNTIME"}
        reps = await svc.runtime.tick()
        return {"ok": True, "sources": {k: {"ingested": v.ingested, "held": v.held, "failed": v.failed} for k, v in reps.items()}}

    @app.post("/pairing")
    async def pairing(body: PairIn) -> dict[str, Any]:
        if svc.runtime is None:
            return {"ok": False, "error": "E_NO_RUNTIME"}
        if body.action == "approve":
            return {"ok": True, "released": svc.runtime.approve_sender(body.platform, body.handle)}
        svc.runtime.block_sender(body.platform, body.handle)
        return {"ok": True}

    @app.get("/notifications")
    async def notifications_get() -> dict[str, Any]:
        if svc.notifications is None or svc.runtime is None:
            return {"ok": False, "error": "E_NO_RUNTIME"}
        h = await svc.notifications.health()
        import json as _json

        return {"ok": True, "available": h.ok, "detail": h.detail, "apps": _json.loads(svc.runtime.state.get("notif_apps", "[]"))}

    @app.post("/notifications")
    async def notifications_set(body: NotifIn) -> dict[str, Any]:
        if svc.runtime is None:
            return {"ok": False, "error": "E_NO_RUNTIME"}
        import json as _json

        apps = sorted({a.strip() for a in body.apps if a.strip() and len(a) < 120})
        svc.runtime.state.set("notif_apps", _json.dumps(apps))
        return {"ok": True, "apps": apps}

    @app.get("/messages")
    async def messages(limit: int = 40, source: str | None = None, q: str | None = None) -> dict[str, Any]:
        if svc.reader is None:
            return {"items": []}
        rows = svc.reader.recent_items(limit=limit, source=source, query=q)
        return {"items": [{**r, "preview": (r["preview"] or "")[:240], "deep_link": r["deep_link"] if str(r["deep_link"] or "").startswith("https://") else None} for r in rows]}

    @app.get("/approvals")
    async def approvals() -> dict[str, Any]:
        return {"items": svc.hub.pending() if svc.hub else []}

    @app.post("/approvals/{approval_id}")
    async def decide(approval_id: str, body: DecideIn) -> dict[str, Any]:
        if svc.hub is None:
            return {"ok": False, "error": "E_NO_AGENT"}
        try:
            return {"ok": svc.hub.decide(approval_id, body.action == "approve", typed_phrase=body.phrase)}
        except Exception as exc:  # noqa: BLE001 - the broker refuses with a code; show it, do not crash
            return {"ok": False, "error": getattr(exc, "code", "E_APPROVAL")}

    @app.get("/chat/{job_id}")
    async def chat_poll(job_id: str) -> dict[str, Any]:
        return svc.chat.poll(job_id) if svc.chat else {"ok": False, "error": "E_NO_AGENT"}

    @app.post("/chat")
    async def chat(body: ChatIn) -> dict[str, Any]:
        if svc.chat is not None:
            return await svc.chat.submit(body.text)
        if svc.answerer is None:
            return {"ok": False, "error": "E_NO_AGENT"}
        a = await svc.answerer.answer(body.text)
        return {"ok": True, "answer": a.text, "citations": a.citations, "grounded": a.grounded, "notes": a.notes}

    return app


def serve(app: FastAPI, port: int = DEFAULT_PORT) -> None:
    import uvicorn

    uvicorn.run(app, host=DEFAULT_HOST, port=port, log_level="warning")
