"""Builds the running agent from what is on this machine: keychain master key, encrypted DB, models.lock, connected sources."""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass
from datetime import date
from pathlib import Path

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
from ragnarok.daemon.chat import ChatService
from ragnarok.daemon.runtime import PAIRED, Governor, Runtime
from ragnarok.harness.answer import Answerer
from ragnarok.harness.executor import ExecContext, Executor
from ragnarok.harness.planner import Planner
from ragnarok.harness.turn import Agent
from ragnarok.llm_gateway.client import Gateway, OllamaGateway
from ragnarok.llm_gateway.models import ModelLock
from ragnarok.memory.commitments import Commitments
from ragnarok.memory.ledger import Ledger
from ragnarok.memory.queue import IngestQueue
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.routes_store import RoutesStore
from ragnarok.memory.runtime_state import RuntimeState
from ragnarok.memory.service import MemoryService
from ragnarok.platform.secrets import SecretStore, default_store
from ragnarok.policy.engine import load_rules
from ragnarok.router.backends.laya import LayaBackend
from ragnarok.router.backends.ollama_small import OllamaSmallBackend
from ragnarok.router.calibration import Calibrator
from ragnarok.router.cascade import Cascade
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
                 "files": ["fs.read", "fs.search", "fs.write", "drive.create"], "code": ["python.run"], "calendar": ["calendar.add"], "system": []}


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
    selector: SkillSelector | None = None
    l1: object | None = None


def _load_index(mem: MemoryService, reader: MemoryReader, index: VectorIndex) -> None:
    for name in mem.with_tx(lambda c: [r[0] for r in c.execute("SELECT name FROM compartments")]):
        cid = reader.compartment_id(name)
        if cid:
            ids, mat = reader.load_embeddings(cid)
            if ids:
                index.load(name, ids, mat)


def build_world(*, use_laya: bool = True, sandbox: bool = True, embed_rungs: bool = True, senders: dict | None = None, vault: SecretStore | None = None, gateway: Gateway | None = None, lock_path: Path | None = None, interval_s: float = 60.0,
                files: list[str] | None = None, max_cpu: float = 60.0) -> World:
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
        runners[name] = ConnectorRunner(mem, queue, build(name, vault), COMPARTMENT, gateway=gw, index=index, require_pairing=name in PAIRED)
    if files:
        runners["files"] = ConnectorRunner(mem, queue, build("files", vault, folders=files), COMPARTMENT, gateway=gw, index=index)
    state = RuntimeState(mem)
    notif = Notifications(default_source(), lambda: json.loads(state.get("notif_apps", "[]")))
    runners["notifications"] = ConnectorRunner(mem, queue, notif, COMPARTMENT, gateway=gw, index=index)
    ledger = Ledger(mem)
    broker = Broker(ledger, keys)
    routes = RoutesStore(mem)
    ws = root / "workspace"
    ws.mkdir(exist_ok=True)
    registry = SkillRegistry(ledger, broker, root / "skills")
    register_builtins(registry)
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
    cascade = Cascade(gateway=gw, selector=selector, l1=l1, l2=small, calibrator=calibrator, library=library, family_skills=FAMILY_SKILLS, l2_timeout_ms=2500, read_only_skills={"memory.search", "reminder.list"}, embed_accept=0.70 if embed_rungs else None, general_below=0.50 if embed_rungs else None)
    answerer = Answerer(reader, index, gw, COMPARTMENT)
    planner = Planner(gw, lambda n: _manifest(registry, n), Agent._now_text)
    agent = Agent(mem=mem, ledger=ledger, reader=reader, index=index, gateway=gw, cascade=cascade, planner=planner, executor=executor, registry=registry, library=library,
                  routes=routes, ctx=ExecContext(compartment=COMPARTMENT, workspace=ws, data_dir=root, today=date.today().isoformat()), answerer=answerer)
    state = RuntimeState(mem)
    notif = Notifications(default_source(), lambda: json.loads(state.get("notif_apps", "[]")))
    runners["notifications"] = ConnectorRunner(mem, queue, notif, COMPARTMENT, gateway=gw, index=index)
    runtime = Runtime(state, runners, interval_s=interval_s, governor=Governor(max_cpu), commitments=Commitments(mem))
    guard = TokenGuard(secrets.token_urlsafe(32))
    token_file = root / "run" / "session.token"
    token_file.write_text(guard.token, encoding="utf-8")
    try:
        token_file.chmod(0o600)
    except OSError:
        pass  # Windows: the per-user data directory ACL applies
    services = Services(runtime, answerer, notif, ChatService(agent), reader, hub)
    w = World(mem, reader, index, gw, runtime, services, guard, token_file, agent, selector, l1)
    w.registry = registry  # type: ignore[attr-defined]
    return w


def _manifest(registry: SkillRegistry, name: str):  # noqa: ANN202
    try:
        return registry.verify_runnable(name).manifest
    except Exception:  # noqa: BLE001
        return None


async def warm_up(world: World) -> None:
    """Index the skill catalogue for shortlisting and preload Laya, off the request path."""
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
