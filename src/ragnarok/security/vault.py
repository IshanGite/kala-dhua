"""Credential vault (D-11, SEC-05): OS secret store only; read and write tokens are SEPARATE items.

The write token is fetched only at the instant of an approved commit, used inside the connector process, then dropped.
Nothing here ever returns a token to a model, a prompt, a log, or a skill environment.
"""

from __future__ import annotations

from ragnarok.core.errors import PolicyDenied
from ragnarok.core.logging import log
from ragnarok.platform.secrets import SecretStore, default_store, token_name


class Vault:
    def __init__(self, store: SecretStore | None = None) -> None:
        self._s = store or default_store()

    def set_token(self, connector: str, scope: str, value: str) -> None:
        self._s.set(token_name(connector, scope), value)

    def read_token(self, connector: str) -> str | None:
        return self._s.get(token_name(connector, "read"))

    def has_write_token(self, connector: str) -> bool:
        return self._s.get(token_name(connector, "write")) is not None

    def write_token_for_commit(self, connector: str, *, approved: bool) -> str:
        """Only callable with proof that the broker approved this exact commit."""
        if not approved:
            raise PolicyDenied("write token requested without an approved commit", code="E_WRITE_TOKEN_UNAPPROVED")
        tok = self._s.get(token_name(connector, "write"))
        if tok is None:
            raise PolicyDenied("no write access has been granted for this connector", code="E_NO_WRITE_GRANT")
        log("write_token_used", connector=connector)
        return tok

    def revoke(self, connector: str) -> None:
        for scope in ("read", "write"):
            self._s.delete(token_name(connector, scope))
