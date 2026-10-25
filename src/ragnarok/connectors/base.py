"""The connector contract (ARCHITECTURE 8.2): describe, connect, sync, fetch, normalize, actions, health.

sync() returns lightweight references plus a new cursor; fetch() gets the content on demand, so a crash between the two
loses nothing: the cursor only advances after the references are queued, and queued references are re-fetched.

Prior work: the per-platform parsing and polling behaviour is adapted from groupify (github.com/abhi-dev99/groupify).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from ragnarok.memory.models import Trust


@dataclass(frozen=True)
class ConnectorInfo:
    name: str
    display: str
    data_kinds: tuple[str, ...]
    read_scopes: tuple[str, ...] = ()
    write_scopes: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()  # egress allowlist for the loopback proxy (verified against official docs at build time)
    default_minimisation: str = ""


@dataclass(frozen=True)
class ItemRef:
    native_id: str
    hint: dict[str, Any] = field(default_factory=dict)
    priority: int = 5  # 1 = live, 5 = backfill


@dataclass
class Cursor:
    value: dict[str, Any] = field(default_factory=dict)


@dataclass
class SyncResult:
    refs: list[ItemRef]
    cursor: Cursor
    more: bool = False


@dataclass
class RawItem:
    """What fetch()+normalize() produce; the ingestion pipeline turns it into memory."""

    source: str
    native_id: str
    text: str
    trust: Trust
    title: str | None = None
    kind: str = "message"
    visibility: Literal["public", "private", "dm"] = "private"
    thread_id: str | None = None
    deep_link: str | None = None
    occurred_at: int | None = None
    html_hidden: bool = False
    strip_quotes: bool = False
    author: str | None = None
    flags: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionSpec:
    """A write action. Every one is tier 2 or higher: prepare -> approval card -> commit (ARCHITECTURE 9)."""

    name: str
    description: str
    parameters: dict[str, Any]
    tier: int = 2


@dataclass(frozen=True)
class Health:
    ok: bool
    detail: str = ""
    quota: str = ""


class Connector(Protocol):
    name: str

    def describe(self) -> ConnectorInfo: ...

    async def sync(self, cursor: Cursor | None) -> SyncResult: ...

    async def fetch(self, ref: ItemRef) -> RawItem | None: ...

    def actions(self) -> list[ActionSpec]: ...

    async def health(self) -> Health: ...
