"""Builds the running agent from what is on this machine: keychain master key, encrypted DB, models.lock, connected sources."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path

from ragnarok.config.paths import data_dir, db_path, ensure_dirs
from ragnarok.connectors.registry import build, connected
from ragnarok.connectors.runner import ConnectorRunner
from ragnarok.daemon.api import Services
from ragnarok.daemon.auth import TokenGuard
from ragnarok.daemon.runtime import PAIRED, Governor, Runtime
from ragnarok.harness.answer import Answerer
from ragnarok.llm_gateway.client import Gateway, OllamaGateway
from ragnarok.llm_gateway.models import ModelLock
from ragnarok.memory.queue import IngestQueue
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.runtime_state import RuntimeState
from ragnarok.memory.service import MemoryService
from ragnarok.platform.secrets import SecretStore, default_store
from ragnarok.store.crypto import KeyHierarchy
from ragnarok.store.vectors import VectorIndex

COMPARTMENT = "work"


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


def _load_index(mem: MemoryService, reader: MemoryReader, index: VectorIndex) -> None:
    for name in mem.with_tx(lambda c: [r[0] for r in c.execute("SELECT name FROM compartments")]):
        cid = reader.compartment_id(name)
        if cid:
            ids, mat = reader.load_embeddings(cid)
            if ids:
                index.load(name, ids, mat)


def build_world(*, vault: SecretStore | None = None, gateway: Gateway | None = None, lock_path: Path | None = None, interval_s: float = 60.0,
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
    runtime = Runtime(RuntimeState(mem), runners, interval_s=interval_s, governor=Governor(max_cpu))
    guard = TokenGuard(secrets.token_urlsafe(32))
    token_file = root / "run" / "session.token"
    token_file.write_text(guard.token, encoding="utf-8")
    try:
        token_file.chmod(0o600)
    except OSError:
        pass  # Windows: the per-user data directory ACL applies
    return World(mem, reader, index, gw, runtime, Services(runtime, Answerer(reader, index, gw, COMPARTMENT)), guard, token_file)


__all__ = ["World", "build_world", "data_dir"]
