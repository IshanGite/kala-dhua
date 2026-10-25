"""Starts the API (with the always-on loop) and, optionally, the always-on-top window."""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
import webbrowser
from collections.abc import AsyncIterator

import uvicorn
from fastapi import FastAPI

from ragnarok.daemon.api import DEFAULT_HOST, create_app
from ragnarok.daemon.wiring import World, warm_up


def make_api(world: World, port: int) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        world.runtime.start()
        if getattr(world.services, "demo", None) is not None:
            world.services.demo.start()
        asyncio.get_running_loop().create_task(warm_up(world))
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


def open_window(port: int, token: str, *, on_top: bool = True, services: object | None = None) -> None:
    url = f"http://{DEFAULT_HOST}:{port}/#token={token}"
    try:
        import webview
    except ImportError:
        webbrowser.open_new_tab(url)  # no native window available: the same page in the default browser
        threading.Event().wait()
        return
    main = webview.create_window("Ragnarok", url, width=390, height=660, min_size=(340, 460), on_top=on_top)
    mascot = webview.create_window("Muninn", f"http://{DEFAULT_HOST}:{port}/mascot#token={token}", width=110, height=112, frameless=True, transparent=True, on_top=True, easy_drag=False,
                                   shadow=False, resizable=False, hidden=True, focus=False, background_color="#000000", text_select=False)
    if services is not None and main is not None and mascot is not None:
        from ragnarok.daemon.shell import Shell

        store = getattr(getattr(services, "runtime", None), "state", None)
        shell = Shell(main, mascot, store)
        shell.load()
        services.shell = shell  # type: ignore[attr-defined]
        main.events.moved += shell.on_moved
        main.events.minimized += shell.on_minimized
        main.events.restored += shell.on_restored
        main.events.closed += lambda: mascot.destroy()
    webview.start()
