"""Starts the API (with the always-on loop) and, optionally, the always-on-top window."""

from __future__ import annotations

import contextlib
import threading
import time
import webbrowser
from collections.abc import AsyncIterator

import uvicorn
from fastapi import FastAPI

from ragnarok.daemon.api import DEFAULT_HOST, create_app
from ragnarok.daemon.wiring import World


def make_api(world: World, port: int) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        world.runtime.start()
        yield
        await world.runtime.stop()

    api = create_app(world.guard, port, world.services)
    api.router.lifespan_context = lifespan
    return api


def start_in_thread(api: FastAPI, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    server = uvicorn.Server(uvicorn.Config(api, host=DEFAULT_HOST, port=port, log_level="warning"))
    t = threading.Thread(target=server.run, name="ragnarok-api", daemon=True)
    t.start()
    for _ in range(150):
        if server.started:
            return server, t
        time.sleep(0.1)
    raise RuntimeError("the local API did not start")


def open_window(port: int, token: str, *, on_top: bool = True) -> None:
    url = f"http://{DEFAULT_HOST}:{port}/#token={token}"
    try:
        import webview
    except ImportError:
        webbrowser.open_new_tab(url)  # no native window available: the same page in the default browser
        threading.Event().wait()
        return
    webview.create_window("Ragnarok", url, width=390, height=660, min_size=(340, 460), on_top=on_top)
    webview.start()
