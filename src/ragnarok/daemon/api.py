"""FastAPI app for the local UI and CLI. Binds 127.0.0.1 only."""

from __future__ import annotations

from fastapi import FastAPI

from ragnarok import __version__
from ragnarok.daemon.auth import LocalOnlyMiddleware, TokenGuard

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def create_app(guard: TokenGuard | None = None, port: int = DEFAULT_PORT) -> FastAPI:
    guard = guard or TokenGuard()
    app = FastAPI(title="Ragnarok", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(LocalOnlyMiddleware, port=port, guard=guard)
    app.state.guard = guard

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/status")
    async def status() -> dict:
        return {"ok": True, "version": __version__, "degraded": []}

    return app


def serve(app: FastAPI, port: int = DEFAULT_PORT) -> None:
    import uvicorn

    uvicorn.run(app, host=DEFAULT_HOST, port=port, log_level="warning")
