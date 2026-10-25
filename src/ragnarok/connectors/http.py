"""Shared HTTP for connectors: timeouts, bounded retries on 429/5xx, structured errors. Never logs URLs with tokens or bodies."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ragnarok.core.errors import RagnarokError


class ConnectorError(RagnarokError):
    def __init__(self, message: str, *, code: str = "E_CONNECTOR", retryable: bool = False) -> None:
        super().__init__(message, code=code)
        self.retryable = retryable


def new_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0), transport=transport, follow_redirects=False)


async def request(client: httpx.AsyncClient, method: str, url: str, *, headers: dict[str, str] | None = None, retries: int = 3, **kw: Any) -> httpx.Response:
    last = 0
    for attempt in range(retries + 1):
        try:
            r = await client.request(method, url, headers=headers, **kw)
        except httpx.TransportError as exc:
            if attempt == retries:
                raise ConnectorError("network error", code="E_NET", retryable=True) from exc
            await asyncio.sleep(min(2**attempt, 8) * 0.25)
            continue
        last = r.status_code
        if r.status_code == 429 or r.status_code >= 500:
            wait = float(r.headers.get("retry-after", "0") or 0)
            if attempt == retries:
                break
            await asyncio.sleep(min(max(wait, 2**attempt * 0.25), 30))
            continue
        if r.status_code in (401, 403):
            raise ConnectorError("not authorised", code="E_AUTH")
        return r
    raise ConnectorError(f"upstream {last}", code="E_UPSTREAM", retryable=True)
