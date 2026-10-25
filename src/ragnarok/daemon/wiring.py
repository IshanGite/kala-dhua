"""Builds the running agent from what is on this machine: keychain master key, encrypted DB, models.lock, connected sources."""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from ragnarok.broker.approvals import Broker
from ragnarok.config.paths import data_dir, db_path, ensure_dirs
from ragnarok.connectors.notifications import Notifications, default_source
from ragnarok.connectors.registry import build, connected
from ragnarok.connectors.runner import ConnectorRunner
from ragnarok.daemon.actions import Actions
from ragnarok.daemon.api import Services
from ragnarok.daemon.approvals import ApprovalHub
from ragnarok.daemon.auth import TokenGuard
from ragnarok.daemon.chat import ChatService, summarize
from ragnarok.daemon.maintenance import Maintenance
from ragnarok.daemon.proactive import Proactive
from ragnarok.daemon.runtime import PAIRED, Governor, Runtime
from ragnarok.daemon.skills_admin import SkillsAdmin
from ragnarok.harness.answer import Answerer
from ragnarok.harness.digest import Digest
from ragnarok.harness.executor import ExecContext, Executor
from ragnarok.harness.memorycmd import MemoryCommands
from ragnarok.harness.planner import Planner
from ragnarok.harness.turn import Agent
from ragnarok.harness.vision import Vision
from ragnarok.llm_gateway.client import Gateway, OllamaGateway
from ragnarok.llm_gateway.models import ModelLock
from ragnarok.memory.commitments import Commitments
from ragnarok.memory.ledger import Ledger
from ragnarok.memory.queue import IngestQueue
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.routes_store import RoutesStore
from ragnarok.memory.runtime_state import RuntimeState
from ragnarok.memory.service import MemoryService
from ragnarok.platform.secrets import SecretStore, default_store, token_name
from ragnarok.policy.engine import load_rules
from ragnarok.router.backends.laya import LayaBackend
from ragnarok.router.backends.ollama_small import OllamaSmallBackend
from ragnarok.router.calibration import Calibrator
from ragnarok.router.cascade import Cascade
from ragnarok.router.intent_rules import match_intent
from ragnarok.router.route_library import RouteLibrary
from ragnarok.router.select import SkillSelector
from ragnarok.sandbox.select import select_backend
from ragnarok.security.guard import Guard
from ragnarok.skills.builtin_loader import register_builtins
from ragnarok.skills.registry import SkillRegistry
from ragnarok.store.crypto import KeyHierarchy
from ragnarok.store.vectors import VectorIndex

COMPARTMENT = "work"
REPO = Path(__file__).resolve().parents[3]
FAMILY_SKILLS = {"mail": ["mail.draft", "mail.send", "mail.reply"], "chat": ["chat.reply"], "notes": ["reminder.add", "reminder.list"], "memory": ["memory.search"],
                 "files": ["fs.read", "fs.search", "fs.write", "drive.create"], "code": ["python.run"], "calendar": ["calendar.add", "calendar.agenda"], "system": []}


@dataclass
class World:
    mem: MemoryService
    reader: MemoryReader
    index: VectorIndex
    gateway: Gateway
    runtime: Runtime
    services: Services
    guard: TokenGuard
    token_file: Path
    agent: Agent | None = None
    ledger: Ledger | None = None
    broker: Broker | None = None
    recovery: Any = None  # the boot recovery report (NFR3.2)
    selector: SkillSelector | None = None
    l1: object | None = None


def _load_index(mem: MemoryService, reader: MemoryReader, index: VectorIndex) -> None:
    for name in mem.with_tx(lambda c: [r[0] for r in c.execute("SELECT name FROM compartments")]):
        cid = reader.compartment_id(name)
        if cid:
            ids, mat = reader.load_embeddings(cid)
            index.load(name, ids, mat) if ids else index.load(name, [], __import__("numpy").zeros((0, 0), dtype="float32"))  # also clears a compartment whose vectors were all deleted


def build_world(*, use_laya: bool = True, sandbox: bool = True, embed_rungs: bool = True, rules: bool = True, senders: dict | None = None, vault: SecretStore | None = None, gateway: Gateway | None = None, lock_path: Path | None = None, interval_s: float = 60.0,
                files: list[str] | None = None, max_cpu: float = 60.0, live_telegram_s: int = 0, intervals: dict[str, float] | None = None) -> World:
    vault = vault or default_store()
    root = ensure_dirs()
    keys = KeyHierarchy.load_or_create(vault)
    mem = MemoryService(db_path(root), keys)
    reader = MemoryReader(mem.db_path, keys)
    index = VectorIndex()
    _load_index(mem, reader, index)
    gw = gateway or OllamaGateway(ModelLock.load(lock_path or Path(__file__).resolve().parents[3] / "models.lock"))
    queue = IngestQueue(mem)
    queue.recover()
    runners: dict[str, ConnectorRunner] = {}
    for name in connected(vault):
        runners[name] = ConnectorRunner(mem, queue, build(name, vault, long_poll_s=live_telegram_s), COMPARTMENT, gateway=gw, index=index, require_pairing=name in PAIRED)
    if files:
        runners["files"] = ConnectorRunner(mem, queue, build("files", vault, folders=files), COMPARTMENT, gateway=gw, index=index)
    vision = Vision(gw)
    state = RuntimeState(mem)
    notif = Notifications(default_source(), lambda: json.loads(state.get("notif_apps", "[]")))
    runners["notifications"] = ConnectorRunner(mem, queue, notif, COMPARTMENT, gateway=gw, index=index)
    ledger = Ledger(mem)
    broker = Broker(ledger, keys)
    routes = RoutesStore(mem)
    ws = root / "workspace"
    ws.mkdir(exist_ok=True)
    registry = SkillRegistry(ledger, broker, root / "skills")
    builtin_names = {r.skill_id for r in register_builtins(registry)}
    budgets = yaml.safe_load((REPO / "src" / "ragnarok" / "policy" / "budgets.yaml").read_text(encoding="utf-8"))
    guard_ = Guard(registry, ledger, load_rules(), workspace_roots=[ws], run_dir=root / "run", budgets=budgets)
    actions = Actions(mem, reader, index, gw, vault, COMPARTMENT, senders=senders)
    hub = ApprovalHub(broker)
    backend = select_backend().backend if sandbox else None
    executor = Executor(ledger=ledger, guard=guard_, broker=broker, registry=registry, backend=backend, natives=actions.natives(), approval_hook=hub.hook)
    selector = SkillSelector(gw)
    small = OllamaSmallBackend(gw)
    l1 = LayaBackend() if (use_laya and LayaBackend.installed()) else small
    cal_file = REPO / "evidence" / "laya_calibration.json"
    calibrator = Calibrator.from_file(cal_file) if cal_file.exists() else Calibrator()
    calibrator.set("skill_family", "ollama_small", 1.0, 0.8)
    calibrator.set("needs_plan", "ollama_small", 1.0, 0.8)

    def hashes(skills: list[str]) -> dict[str, str]:
        return {sk: (ledger.get_skill(sk) or {}).get("tree_hash", "") for sk in skills}

    library = RouteLibrary(routes, skill_hashes=hashes)
    cascade = Cascade(gateway=gw, selector=selector, l1=l1, l2=small, calibrator=calibrator, library=library, family_skills=FAMILY_SKILLS, l2_timeout_ms=2500, read_only_skills={"memory.search", "reminder.list"}, embed_accept=0.70 if embed_rungs else None, general_below=0.50 if embed_rungs else None, intent_rules=match_intent if rules else None, l2_skill_only=True)
    digest = Digest(reader, gw, COMPARTMENT)
    answerer = Answerer(reader, index, gw, COMPARTMENT, digest=digest)
    planner = Planner(gw, lambda n: _manifest(registry, n), Agent._now_text)
    agent = Agent(mem=mem, ledger=ledger, reader=reader, index=index, gateway=gw, cascade=cascade, planner=planner, executor=executor, registry=registry, library=library,
                  routes=routes, ctx=ExecContext(compartment=COMPARTMENT, workspace=ws, data_dir=root, today=date.today().isoformat()), answerer=answerer, fast_refuse=True, memcmd=MemoryCommands(mem, reader, COMPARTMENT), vision=vision, calendar_mirror=lambda: vault.get(token_name("google", "write")) is not None)
    state = RuntimeState(mem)
    notif = Notifications(default_source(), lambda: json.loads(state.get("notif_apps", "[]")))
    runners["notifications"] = ConnectorRunner(mem, queue, notif, COMPARTMENT, gateway=gw, index=index)
    runtime = Runtime(state, runners, interval_s=interval_s, governor=Governor(max_cpu), commitments=Commitments(mem), intervals=intervals)
    async def run_command(cmd: str) -> str:
        return str(summarize(await agent.handle(cmd, trust="agent")).get("text", ""))  # a trigger's task is not more trusted than a typed one: sends still wait for a card

    async def agenda_today() -> str:
        return (await answerer.agenda("today")).text

    proactive = Proactive(state, reader, run_command=run_command, agenda=agenda_today)
    runtime.proactive = proactive
    runtime.digest = digest
    runtime.maintenance = Maintenance(mem, state, proactive.inbox)
    guard = TokenGuard(secrets.token_urlsafe(32))
    token_file = root / "run" / "session.token"
    token_file.write_text(guard.token, encoding="utf-8")
    try:
        token_file.chmod(0o600)
    except OSError:
        pass  # Windows: the per-user data directory ACL applies
    chat = ChatService(agent, hub, proactive.triggers)
    chat.restore()
    def reindex() -> None:
        _load_index(mem, reader, index)

    skills_admin = SkillsAdmin(registry, ledger, broker, hub, selector, gw, builtin_names)
    services = Services(runtime, answerer, notif, chat, reader, hub, proactive, mem, reindex, skills_admin, digest, vision)
    w = World(mem, reader, index, gw, runtime, services, guard, token_file, agent, ledger, broker, None, selector, l1)
    w.registry = registry  # type: ignore[attr-defined]
    return w


def _manifest(registry: SkillRegistry, name: str):  # noqa: ANN202
    try:
        return registry.verify_runnable(name).manifest
    except Exception:  # noqa: BLE001
        return None


async def warm_up(world: World) -> None:
    """Recover from a crash first (NFR3.2), then index the skill catalogue for shortlisting and preload Laya, off the request path."""
    from ragnarok.harness.recovery import recover

    if world.agent is not None and world.ledger is not None and world.broker is not None:
        rep = await recover(world.ledger, world.agent.executor, world.broker, lambda _t: world.agent.ctx, run=False)  # state restored: nodes requeued, stale approvals expired
        world.recovery = rep
        if rep.resumed:
            world.runtime._note("recovered", "tasks", "ok", resumed=len(rep.resumed), requeued=rep.requeued_nodes, ms=round(rep.restore_ms))
            for tid in rep.resumed:  # finish the interrupted work in the background; anything needing a click asks again
                asyncio.get_running_loop().create_task(world.agent.executor.run_task(tid, world.agent.ctx))
    reg = world.registry  # type: ignore[attr-defined]
    manifests = [m for r in world.agent.ledger.list_skills() if (m := _manifest(reg, r["skill_id"])) is not None] if world.agent else []
    from ragnarok.core.clock import get_clock

    try:
        await world.selector.index(manifests, get_clock().monotonic() + 60)  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 - routing degrades to the planner, never blocks startup
        pass
    if hasattr(world.l1, "warmup"):
        asyncio.get_running_loop().create_task(world.l1.warmup())  # type: ignore[union-attr]


__all__ = ["World", "build_world", "data_dir", "warm_up"]
