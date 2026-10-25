"""Secret store: macOS Keychain or Windows Credential Manager through `keyring`.

Windows credentials are limited to roughly 2.5 KB, so values are split into small chunks. Read and
write tokens for a connector are separate items (D-11): use token_name(connector, "read") and
token_name(connector, "write").
"""

from __future__ import annotations

import os
from typing import Protocol

SERVICE = "ragnarok"
CHUNK = 900


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


def token_name(connector: str, scope: str) -> str:
    if scope not in ("read", "write"):
        raise ValueError("scope must be 'read' or 'write'")
    return f"token/{connector}/{scope}"


class MemoryStore:
    """In-process store for tests and CI. Never used for real secrets."""

    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._d.get(name)

    def set(self, name: str, value: str) -> None:
        self._d[name] = value

    def delete(self, name: str) -> None:
        self._d.pop(name, None)


class KeyringStore:
    def __init__(self, service: str = SERVICE) -> None:
        import keyring

        self._kr = keyring
        self._service = service

    def get(self, name: str) -> str | None:
        head = self._kr.get_password(self._service, name)
        if head is None:
            return None
        if not head.startswith("#chunks:"):
            return head
        count = int(head.split(":", 1)[1])
        parts = []
        for i in range(count):
            part = self._kr.get_password(self._service, f"{name}#{i}")
            if part is None:
                return None
            parts.append(part)
        return "".join(parts)

    def set(self, name: str, value: str) -> None:
        self.delete(name)
        if len(value) <= CHUNK:
            self._kr.set_password(self._service, name, value)
            return
        pieces = [value[i : i + CHUNK] for i in range(0, len(value), CHUNK)]
        for i, piece in enumerate(pieces):
            self._kr.set_password(self._service, f"{name}#{i}", piece)
        self._kr.set_password(self._service, name, f"#chunks:{len(pieces)}")

    def delete(self, name: str) -> None:
        head = self._kr.get_password(self._service, name)
        if head is None:
            return
        if head.startswith("#chunks:"):
            for i in range(int(head.split(":", 1)[1])):
                try:
                    self._kr.delete_password(self._service, f"{name}#{i}")
                except Exception:  # noqa: BLE001 - already gone
                    pass
        try:
            self._kr.delete_password(self._service, name)
        except Exception:  # noqa: BLE001
            pass


_default: SecretStore | None = None


def default_store() -> SecretStore:
    global _default
    if _default is None:
        _default = MemoryStore() if os.environ.get("RAGNAROK_SECRETS") == "memory" else KeyringStore()
    return _default


def set_default_store(store: SecretStore | None) -> None:
    global _default
    _default = store
