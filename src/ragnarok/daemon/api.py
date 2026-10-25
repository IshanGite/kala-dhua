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
    proactive: Any = None
    mem: Any = None  # MemoryService, for delete
    reindex: Any = None  # callable: rebuild the in-memory vector index after a delete
    skills: Any = None  # SkillsAdmin
    digest: Any = None  # the digest engine
    vision: Any = None  # explain the screen / ask about an image
    demo: Any = None  # demo mode state (synthetic data, fake sends), or None
    voice: Any = None  # the microphone recorder
    showcase: Any = None  # one button per feature, for demos
    shell: Any = None  # the window shell: pin, snap, desktops, the raven (None when there is no native window)


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    quick_s: float | None = Field(default=None, ge=0, le=10)  # how long to wait for an immediate answer before returning a job to poll (the window uses a short wait so Stop is available at once)


class MascotMove(BaseModel):
    dx: int = Field(ge=-400, le=400)
    dy: int = Field(ge=-400, le=400)


class SpeakIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class MascotSize(BaseModel):
    w: int = Field(ge=20, le=420)
    h: int = Field(ge=20, le=300)


class ToggleIn(BaseModel):
    enabled: bool


class NotifIn(BaseModel):
    apps: list[str] = Field(max_length=50)


class ProSettingsIn(BaseModel):
    briefing_at: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    briefing_off: bool = False
    os_notify: bool | None = None
    enabled: bool | None = None


class DeleteIn(BaseModel):
    item_ids: list[str] = Field(default_factory=list, max_length=200)
    fact_ids: list[str] = Field(default_factory=list, max_length=200)
    source: str | None = Field(default=None, max_length=40)
    token: str | None = Field(default=None, max_length=200)


class FactEditIn(BaseModel):
    value: str = Field(min_length=1, max_length=2000)


class SkillPathIn(BaseModel):
    path: str = Field(min_length=1, max_length=500)


class VisionIn(BaseModel):
    question: str | None = Field(default=None, max_length=500)
    image_b64: str | None = Field(default=None, max_length=17_000_000)  # a picture the user attached; empty means: look at the screen
    delay_s: float = Field(default=0.0, ge=0, le=10)
    mode: str = Field(default="screen", pattern="^(screen|region|window)$")  # a whole display, a part you drag out, or a window you click
    display: int = Field(default=1, ge=1, le=8)
    engine: str = Field(default="auto", pattern="^(auto|read|deep)$")  # read = OS text reader + language model (fast), deep = visual model, auto = read first


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
    app.add_middleware(LocalOnlyMiddleware, port=port, guard=guard, public_paths=frozenset({"/healthz", "/", "/mascot"}))
    app.state.guard, app.state.services = guard, svc

    ui_file = Path(__file__).resolve().parents[1] / "ui" / "index.html"

    @app.get("/", response_class=HTMLResponse)
    async def ui() -> str:
        return ui_file.read_text(encoding="utf-8")  # no secrets in the page; the session token arrives in the URL fragment from the launcher

    mascot_file = Path(__file__).resolve().parents[1] / "ui" / "mascot.html"

    @app.get("/mascot", response_class=HTMLResponse)
    async def mascot_page() -> str:
        return mascot_file.read_text(encoding="utf-8")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/status")
    async def status() -> dict[str, Any]:
        out: dict[str, Any] = {"ok": True, "version": __version__, "degraded": [], "demo": svc.demo is not None, "demo_ready": bool(svc.demo and svc.demo.ready)}
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

    @app.get("/inbox")
    async def inbox() -> dict[str, Any]:
        return {"items": svc.proactive.inbox.items() if svc.proactive else [], "settings": svc.proactive.settings() if svc.proactive else {}}

    @app.post("/inbox/dismiss")
    async def inbox_dismiss(item_id: str | None = None) -> dict[str, Any]:
        return {"ok": True, "dismissed": svc.proactive.inbox.dismiss(item_id) if svc.proactive else 0}

    @app.get("/triggers")
    async def triggers_list() -> dict[str, Any]:
        return {"items": svc.proactive.triggers.all() if svc.proactive else []}

    @app.delete("/triggers/{tid}")
    async def triggers_delete(tid: int) -> dict[str, Any]:
        return {"ok": True, "removed": svc.proactive.triggers.remove(tid) if svc.proactive else 0}

    @app.post("/proactive")
    async def proactive_settings(body: ProSettingsIn) -> dict[str, Any]:
        if svc.proactive is None:
            return {"ok": False, "error": "E_NO_RUNTIME"}
        svc.proactive.configure(briefing_at=None if body.briefing_off else (body.briefing_at if body.briefing_at else False), os_notify=body.os_notify, enabled=body.enabled)
        return {"ok": True, "settings": svc.proactive.settings()}

    @app.get("/memory/facts")
    async def memory_facts() -> dict[str, Any]:
        if svc.reader is None:
            return {"items": []}
        cid = svc.reader.compartment_id("work")
        rows = svc.reader.facts(cid) if cid else []
        return {"items": [{"id": f["id"], "kind": f["kind"], "subject": f["subject"], "predicate": f["predicate"], "value": f["value"], "trust": f["trust"], "learned_at": f["learned_at"],
                           "pinned": bool(f["pinned"]), "evidence": len(svc.reader.provenance_of("fact", f["id"]))} for f in rows[:200]]}

    @app.post("/memory/facts/{fact_id}")
    async def memory_fact_edit(fact_id: str, body: FactEditIn) -> dict[str, Any]:
        if svc.mem is None:
            return {"ok": False, "error": "E_NO_AGENT"}
        svc.mem.edit_fact(fact_id, body.value)  # pinned and attributed to the user: forgetting never overrides a user edit
        return {"ok": True}

    @app.post("/memory/delete/preview")
    async def memory_delete_preview(body: DeleteIn) -> dict[str, Any]:
        from ragnarok.memory.delete import Deleter, DeleteSelector

        if svc.mem is None:
            return {"ok": False, "error": "E_NO_AGENT"}
        p = Deleter(svc.mem).preview(DeleteSelector(item_ids=tuple(body.item_ids), fact_ids=tuple(body.fact_ids), source=body.source))
        return {"ok": True, "counts": p.counts, "summary": p.summary, "token": p.confirm_token}

    @app.post("/memory/delete")
    async def memory_delete(body: DeleteIn) -> dict[str, Any]:
        from ragnarok.memory.delete import ConfirmRequired, Deleter, DeleteSelector

        if svc.mem is None:
            return {"ok": False, "error": "E_NO_AGENT"}
        try:
            r = Deleter(svc.mem).delete(DeleteSelector(item_ids=tuple(body.item_ids), fact_ids=tuple(body.fact_ids), source=body.source), body.token or "")
        except ConfirmRequired:
            return {"ok": False, "error": "E_CONFIRM_REQUIRED"}
        if svc.reindex:
            svc.reindex()
        return {"ok": True, "counts": r.counts, "verified": r.verified, "residual": r.residual}

    @app.post("/memory/export")
    async def memory_export(fmt: str = "json") -> dict[str, Any]:
        from datetime import datetime
        from pathlib import Path

        from ragnarok.memory.export import dumps, export_all, to_markdown

        if svc.reader is None:
            return {"ok": False, "error": "E_NO_AGENT"}
        data = export_all(svc.reader)
        out_dir = Path.home() / "Downloads"
        out_dir = out_dir if out_dir.is_dir() else Path.home()
        path = out_dir / f"ragnarok-export-{datetime.now().strftime('%Y%m%d-%H%M%S')}.{'md' if fmt == 'md' else 'json'}"
        path.write_text(to_markdown(data) if fmt == "md" else dumps(data), encoding="utf-8")
        return {"ok": True, "path": str(path), "items": len(data["items"]), "facts": len(data["facts"]), "events": len(data["events"])}

    @app.get("/trace")
    async def trace(limit: int = 20) -> dict[str, Any]:
        return {"items": svc.chat.recent_trace(limit) if svc.chat else []}

    @app.get("/skills")
    async def skills_list() -> dict[str, Any]:
        if svc.skills is None:
            return {"items": []}
        await svc.skills.reindex()
        return {"items": svc.skills.list()}

    @app.post("/skills/register")
    async def skills_register(body: SkillPathIn) -> dict[str, Any]:
        if svc.skills is None:
            return {"ok": False, "error": "E_NO_AGENT"}
        try:
            return {"ok": True, **svc.skills.register(body.path)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": getattr(exc, "code", "E_SKILL"), "detail": str(exc)[:200]}

    @app.post("/skills/{name}/enable")
    async def skills_enable(name: str) -> dict[str, Any]:
        if svc.skills is None:
            return {"ok": False, "error": "E_NO_AGENT"}
        try:
            return await svc.skills.enable(name)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": getattr(exc, "code", "E_SKILL"), "detail": str(exc)[:200]}

    @app.post("/skills/{name}/disable")
    async def skills_disable(name: str) -> dict[str, Any]:
        if svc.skills is None:
            return {"ok": False, "error": "E_NO_AGENT"}
        await svc.skills.disable(name)
        return {"ok": True}

    @app.delete("/skills/{name}")
    async def skills_remove(name: str) -> dict[str, Any]:
        if svc.skills is None:
            return {"ok": False, "error": "E_NO_AGENT"}
        try:
            await svc.skills.remove(name)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": getattr(exc, "code", "E_SKILL")}
        return {"ok": True}

    @app.get("/digest")
    async def digest(hours: float = 24, sources: str | None = None, topic: str | None = None) -> dict[str, Any]:
        if svc.digest is None:
            return {"sections": [], "timeline": [], "overview": "The digest is not available.", "items": 0}
        return svc.digest.build(max(0.25, min(hours, 24 * 30)), sources={s for s in sources.split(",") if s} if sources else None, topic=topic[:80] if topic else None)

    @app.post("/system/open")
    async def system_open(pane: str) -> dict[str, Any]:
        """Opens one fixed System Settings page the app needs (never a path or URL from the caller)."""
        import subprocess
        import sys as _sys

        panes = {"files": "Privacy_AllFiles", "screen": "Privacy_ScreenCapture", "notifications": "Privacy_AllFiles"}
        if pane not in panes or _sys.platform != "darwin":
            return {"ok": False, "error": "E_UNSUPPORTED"}
        subprocess.run(["open", f"x-apple.systempreferences:com.apple.preference.security?{panes[pane]}"], check=False, timeout=10)  # noqa: S603, S607
        return {"ok": True}

    @app.get("/showcase")
    async def showcase_list() -> dict[str, Any]:
        return {"items": svc.showcase.items() if svc.showcase else []}

    @app.post("/showcase/{name}")
    async def showcase_run(name: str) -> dict[str, Any]:
        """Runs one feature directly (no sentence to misread) as a chat job, so its result and any approval cards show in the chat."""
        if svc.chat is None or svc.showcase is None or name not in {i["id"] for i in svc.showcase.items()}:
            return {"ok": False, "error": "E_NO_SUCH_ITEM"}
        return await svc.chat.submit_showcase(name, svc.showcase)

    @app.get("/reminders")
    async def reminders_list() -> dict[str, Any]:
        return {"items": svc.runtime.commitments.list_open() if svc.runtime and svc.runtime.commitments else []}

    @app.post("/reminders/{rid}/done")
    async def reminder_done(rid: str) -> dict[str, Any]:
        if not svc.runtime or not svc.runtime.commitments:
            return {"ok": False, "error": "E_NO_RUNTIME"}
        svc.runtime.commitments.remove(rid)
        return {"ok": True}

    @app.post("/reminders/{rid}/snooze")
    async def reminder_snooze(rid: str, minutes: int = 10) -> dict[str, Any]:
        """Moves the reminder later: the old one is removed and an identical one is created for the new time."""
        if not svc.runtime or not svc.runtime.commitments:
            return {"ok": False, "error": "E_NO_RUNTIME"}
        from ragnarok.core.clock import get_clock as _clock

        row = next((r for r in svc.runtime.commitments.list_open() if r["id"] == rid), None)
        if row is None:
            return {"ok": False, "error": "E_NO_REMINDER"}
        base = max(row["due_at"] or 0, _clock().now_ms())
        svc.runtime.commitments.remove(rid)
        svc.runtime.commitments.add("work", row["what"], base + max(1, min(minutes, 60 * 24 * 7)) * 60_000)
        return {"ok": True}

    @app.post("/speak")
    async def speak(body: SpeakIn) -> dict[str, Any]:
        """Reads a reply aloud with the system voice (macOS `say`), on this computer. One voice at a time."""
        import sys as _sys

        if _sys.platform != "darwin":
            return {"ok": False, "error": "E_UNSUPPORTED"}
        import subprocess as _sp

        prev = getattr(app.state, "say", None)
        if prev is not None and prev.poll() is None:
            prev.terminate()
        clean = "".join(ch for ch in body.text if ch.isprintable() or ch in "\n ")[:1200]
        app.state.say = _sp.Popen(["say", "--", clean], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)  # noqa: S603, S607
        return {"ok": True}

    @app.post("/speak/stop")
    async def speak_stop() -> dict[str, Any]:
        prev = getattr(app.state, "say", None)
        if prev is not None and prev.poll() is None:
            prev.terminate()
        return {"ok": True}

    @app.get("/shell/state")
    async def shell_state() -> dict[str, Any]:
        return svc.shell.state() if svc.shell else {"available": False}

    @app.post("/shell/pin")
    async def shell_pin(edge: str | None = None) -> dict[str, Any]:
        return svc.shell.pin(edge) if svc.shell else {"ok": False, "error": "E_NO_WINDOW"}

    @app.post("/shell/prompt")
    async def shell_prompt(edge: str = "right") -> dict[str, Any]:
        """Shows the window options (pin, split screen, desktops) as if the window had just been dragged to that edge."""
        if not svc.shell:
            return {"ok": False, "error": "E_NO_WINDOW"}
        svc.shell.prompt_edge = edge if edge in ("left", "right", "top", "bottom") else "right"
        return {"ok": True}

    @app.get("/shell/poll")
    async def shell_poll() -> dict[str, Any]:
        return svc.shell.take_prompt() if svc.shell else {"available": False}

    @app.post("/shell/minimize")
    async def shell_minimize() -> dict[str, Any]:
        """Minimize the window; the raven waits where it was pinned until it is clicked."""
        if not svc.shell:
            return {"ok": False, "error": "E_NO_WINDOW"}
        return svc.shell.minimize()

    @app.post("/shell/restore")
    async def shell_restore() -> dict[str, Any]:
        return svc.shell.restore() if svc.shell else {"ok": False, "error": "E_NO_WINDOW"}

    @app.post("/shell/snap")
    async def shell_snap(side: str = "right") -> dict[str, Any]:
        return svc.shell.snap("left" if side == "left" else "right") if svc.shell else {"ok": False, "error": "E_NO_WINDOW"}

    @app.post("/shell/desktops")
    async def shell_desktops(on: bool = True) -> dict[str, Any]:
        return svc.shell.all_desktops(on) if svc.shell else {"ok": False, "error": "E_NO_WINDOW"}

    @app.post("/shell/newdesktop")
    async def shell_newdesktop() -> dict[str, Any]:
        return svc.shell.new_desktop() if svc.shell else {"ok": False, "error": "E_NO_WINDOW"}

    @app.post("/shell/mascot/move")
    async def shell_mascot_move(body: MascotMove) -> dict[str, Any]:
        if svc.shell:
            svc.shell.move_mascot(body.dx, body.dy)
        return {"ok": bool(svc.shell)}

    @app.post("/shell/mascot/size")
    async def shell_mascot_size(body: MascotSize) -> dict[str, Any]:
        if svc.shell:
            svc.shell.resize_mascot(body.w, body.h)
        return {"ok": bool(svc.shell)}

    @app.post("/shell/mascot/dropped")
    async def shell_mascot_dropped() -> dict[str, Any]:
        if svc.shell:
            svc.shell.mascot_dropped()
        return {"ok": bool(svc.shell)}

    @app.get("/demo/outbox")
    async def demo_outbox() -> dict[str, Any]:
        return {"demo": svc.demo is not None, "items": list(svc.demo.outbox) if svc.demo else [], "auto": bool(svc.demo and svc.demo.auto)}

    @app.post("/demo/incoming")
    async def demo_incoming() -> dict[str, Any]:
        if svc.demo is None:
            return {"ok": False, "error": "E_NOT_DEMO"}
        return {"ok": True, **await svc.demo.incoming()}

    @app.post("/demo/auto")
    async def demo_auto(enabled: bool) -> dict[str, Any]:
        if svc.demo is None:
            return {"ok": False, "error": "E_NOT_DEMO"}
        svc.demo.auto = enabled
        return {"ok": True, "auto": enabled}

    @app.get("/voice/status")
    async def voice_status() -> dict[str, Any]:
        import asyncio as _a

        from ragnarok.platform import voice as v

        if not v.available():
            return {"available": False}
        perms = await _a.to_thread(v.permissions)
        return {"available": True, "engine": "whisper" if v.whisper_ready() else "apple", "multilingual": v.whisper_ready(), "permissions": perms, "recording": bool(svc.voice and svc.voice.active)}

    @app.post("/voice/start")
    async def voice_start() -> dict[str, Any]:
        import asyncio as _a

        from ragnarok.platform import voice as v

        if svc.voice is None:
            svc.voice = v.Recorder()
        try:
            await _a.to_thread(svc.voice.start, 60.0)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": getattr(exc, "code", "E_VOICE"), "detail": str(exc)[:300]}
        return {"ok": True}

    @app.post("/voice/stop")
    async def voice_stop() -> dict[str, Any]:
        import asyncio as _a

        if svc.voice is None or not svc.voice.active:
            return {"ok": False, "error": "E_VOICE", "detail": "nothing is being recorded"}
        try:
            d = await _a.to_thread(svc.voice.stop, None)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": getattr(exc, "code", "E_VOICE"), "detail": str(exc)[:300]}
        return {"ok": True, **d}

    @app.post("/voice/cancel")
    async def voice_cancel() -> dict[str, Any]:
        if svc.voice is not None:
            svc.voice.cancel()
        return {"ok": True}

    @app.post("/voice/permissions")
    async def voice_permissions() -> dict[str, Any]:
        import asyncio as _a

        from ragnarok.platform import voice as v

        return {"ok": True, "granted": await _a.to_thread(v.request_permissions)}

    @app.get("/screens")
    async def screens(check: bool = False) -> dict[str, Any]:
        """The connected displays, and (with check=true) whether macOS lets this app see the screen."""
        import asyncio as _a

        from ragnarok.platform import ocr as _ocr
        from ragnarok.platform.screen import list_displays, permission_ok

        out: dict[str, Any] = {"displays": await _a.to_thread(list_displays), "text_reader": _ocr.available()}
        if check:
            out["permission"] = await _a.to_thread(permission_ok)
        return out

    @app.post("/vision")
    async def vision(body: VisionIn) -> dict[str, Any]:
        """Typed or clicked by the user only. The picture is used once and never stored; the answer is words, and nothing is executed from it. A job, so it can be stopped."""
        if svc.chat is None:
            return {"ok": False, "error": "E_NO_AGENT"}
        import base64 as _b

        image = None
        if body.image_b64:
            try:
                image = _b.b64decode(body.image_b64.split(",")[-1], validate=False)
            except ValueError:
                return {"ok": False, "error": "E_IMAGE_TYPE", "detail": "that is not a readable image"}
        return await svc.chat.submit_vision(body.question, image, body.delay_s, mode=body.mode, display=body.display, engine=body.engine)

    @app.post("/chat/{job_id}/cancel")
    async def chat_cancel(job_id: str) -> dict[str, Any]:
        return svc.chat.cancel(job_id) if svc.chat else {"ok": False, "error": "E_NO_AGENT"}

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
            return await svc.chat.submit(body.text, quick_s=body.quick_s)
        if svc.answerer is None:
            return {"ok": False, "error": "E_NO_AGENT"}
        a = await svc.answerer.answer(body.text)
        return {"ok": True, "answer": a.text, "citations": a.citations, "grounded": a.grounded, "notes": a.notes}

    return app


def serve(app: FastAPI, port: int = DEFAULT_PORT) -> None:
    import uvicorn

    uvicorn.run(app, host=DEFAULT_HOST, port=port, log_level="warning")
