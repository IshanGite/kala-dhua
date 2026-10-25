"""The window shell: pinning Ragnarok to a screen edge, snapping it to half the screen, showing it on every desktop, and the small raven (Muninn) that waits where it is pinned.

Everything here is about WINDOWS. It never touches memory, models or the policy engine. The window and the raven are two pywebview windows owned by one controller;
the API exposes `/shell/*` so the page, the raven and tests all use the same few actions. Windows/Linux: the window moves work through pywebview; the macOS-only parts (every desktop,
next desktop) are in `_mac_*` and are no-ops elsewhere (Tushar)."""

from __future__ import annotations

import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Any

from ragnarok.core.logging import log

MASCOT_W, MASCOT_H = 72, 92  # the idle size: a small bolt; the window grows only while it is speaking
EDGE_NEAR = 28  # how close to an edge (points) a dragged window must come before the options appear
EDGES = ("left", "right", "top", "bottom")


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int  # a rectangle in global screen points, origin top-left


def _mac_insets(screens: list[Any]) -> list[tuple[int, int, int, int]]:
    """(left, top, right, bottom) the menu bar and the Dock take from each screen, in points. Zeros if it cannot be read."""
    out = [(0, 0, 0, 0)] * len(screens)
    if sys.platform != "darwin":
        return out
    try:
        from AppKit import NSScreen

        ns = list(NSScreen.screens())
        res = []
        for i, _s in enumerate(screens):
            if i >= len(ns):
                res.append((0, 0, 0, 0))
                continue
            f, v = ns[i].frame(), ns[i].visibleFrame()
            left = int(v.origin.x - f.origin.x)
            bottom = int(v.origin.y - f.origin.y)
            right = int((f.origin.x + f.size.width) - (v.origin.x + v.size.width))
            top = int((f.origin.y + f.size.height) - (v.origin.y + v.size.height))
            res.append((max(left, 0), max(top, 0), max(right, 0), max(bottom, 0)))
        return res
    except Exception:  # noqa: BLE001
        return out


def _mac_all_desktops(title: str, on: bool) -> bool:
    """Show the window on every desktop (Space), including over full-screen apps."""
    if sys.platform != "darwin":
        return False
    try:
        from AppKit import (
            NSApp,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorMoveToActiveSpace,
        )
        from PyObjCTools import AppHelper

        def apply() -> None:
            for w in NSApp.windows():
                if str(w.title()) == title or (title == "Muninn" and str(w.title()) in ("Muninn", "")):
                    w.setCollectionBehavior_((NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary) if on else NSWindowCollectionBehaviorMoveToActiveSpace)

        AppHelper.callAfter(apply)
        return True
    except Exception as exc:  # noqa: BLE001
        log("all_desktops_failed", err=type(exc).__name__)
        return False


def _mac_next_desktop() -> bool:
    """Switch to the next desktop (Control+Right). Needs the Accessibility permission for the app that started Ragnarok."""
    if sys.platform != "darwin":
        return False
    r = subprocess.run(["osascript", "-e", 'tell application "System Events" to key code 124 using control down'], capture_output=True, timeout=8, check=False)  # noqa: S603, S607
    return r.returncode == 0


class Shell:
    """Owns the main window and the raven window. All methods are safe to call from any thread."""

    def __init__(self, main: Any, mascot: Any, store: Any = None) -> None:
        self.main, self.mascot, self._store = main, mascot, store
        self.pinned: str | None = None
        self.pin_at: tuple[int, int] | None = None  # where the raven sits (top-left)
        self.edge = "right"
        self._prompted: tuple[str, float] | None = None
        self.prompt_edge: str | None = None  # set when the window stops near an edge; the page picks it up by polling (the page's security policy forbids evaluate_js)
        self._timer: threading.Timer | None = None
        self._all_desktops = False
        self._home: Box | None = None  # where the window was before it was pinned
        self._hidden_by_pin = False
        self.mw, self.mh = MASCOT_W, MASCOT_H
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ geometry
    def _screens(self) -> list[tuple[Box, tuple[int, int, int, int]]]:
        import webview

        scr = list(webview.screens)
        ins = _mac_insets(scr)
        return [(Box(int(s.x), int(s.y), int(s.width), int(s.height)), ins[i]) for i, s in enumerate(scr)] or [(Box(0, 0, 1440, 900), (0, 38, 0, 70))]

    def _screen_of(self, cx: int, cy: int) -> tuple[Box, tuple[int, int, int, int]]:
        scs = self._screens()
        for b, ins in scs:
            if b.x <= cx < b.x + b.w and b.y <= cy < b.y + b.h:
                return b, ins
        return scs[0]

    def _main_box(self) -> Box:
        return Box(int(self.main.x), int(self.main.y), int(self.main.width), int(self.main.height))

    def near_edge(self, b: Box | None = None) -> str | None:
        b = b or self._main_box()
        scr, (il, it, ir, ib) = self._screen_of(b.x + b.w // 2, b.y + b.h // 2)
        left, top, right, bottom = scr.x + il, scr.y + it, scr.x + scr.w - ir, scr.y + scr.h - ib
        d = {"left": b.x - left, "right": right - (b.x + b.w), "top": b.y - top, "bottom": bottom - (b.y + b.h)}
        edge, dist = min(d.items(), key=lambda kv: kv[1])
        return edge if dist <= EDGE_NEAR else None

    def mascot_spot(self, edge: str, b: Box) -> tuple[int, int]:
        scr, (il, it, ir, ib) = self._screen_of(b.x + b.w // 2, b.y + b.h // 2)
        left, top, right, bottom = scr.x + il, scr.y + it, scr.x + scr.w - ir, scr.y + scr.h - ib
        mw, mh = MASCOT_W, MASCOT_H
        cy = min(max(b.y + b.h // 2 - mh // 2, top), bottom - mh)
        cx = min(max(b.x + b.w // 2 - mw // 2, left), right - mw)
        if edge == "left":
            return left - 4, cy
        if edge == "right":
            return right - mw + 4, cy
        if edge == "top":
            return cx, top - 4
        return cx, bottom - mh + 2  # bottom: it stands on the Dock line

    # ------------------------------------------------------------------ the moved-near-an-edge prompt
    def on_moved(self, _x: int, _y: int) -> None:
        """Called by pywebview while the window moves. When it stops near an edge the page offers: pin here, snap to half, every desktop."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(0.8, self._maybe_prompt)
            self._timer.daemon = True
            self._timer.start()

    def _maybe_prompt(self) -> None:
        if self.pinned:
            return
        try:
            edge = self.near_edge()
        except Exception:  # noqa: BLE001
            return
        if edge is None:
            self._prompted = None
            return
        if self._prompted and self._prompted[0] == edge:
            return  # already asked for this edge: not again until the window leaves it
        self._prompted = (edge, 0.0)
        self.prompt_edge = edge

    # ------------------------------------------------------------------ actions
    def take_prompt(self) -> dict[str, Any]:
        """What the pages poll for: an edge to offer options for (once), and which edge the raven sits on (so it faces into the screen)."""
        p, self.prompt_edge = self.prompt_edge, None
        return {"prompt": p, "edge": self.edge, "pinned": self.pinned}

    def state(self) -> dict[str, Any]:
        try:
            b = self._main_box()
            edge = self.near_edge(b)
        except Exception:  # noqa: BLE001
            b, edge = Box(0, 0, 0, 0), None
        return {"pinned": self.pinned, "edge": self.edge, "near_edge": edge, "all_desktops": self._all_desktops, "window": {"x": b.x, "y": b.y, "w": b.w, "h": b.h}}

    def pin(self, edge: str | None = None) -> dict[str, Any]:
        """Hide the window and let the raven wait at the edge."""
        with self._lock:
            b = self._main_box()
            edge = edge if edge in EDGES else (self.near_edge(b) or "right")
            self._home, self.edge, self.pinned = b, edge, edge
            x, y = self.mascot_spot(edge, b)
            self.pin_at = (x, y)
            self._show_mascot(x, y)
            self.main.hide()
            self._hidden_by_pin = True
            self._remember()
        return {"ok": True, **self.state()}

    def _show_mascot(self, x: int, y: int) -> None:
        self.mascot.resize(MASCOT_W, MASCOT_H)
        self.mascot.move(int(x), int(y))
        self.mascot.show()
        if self._all_desktops:
            _mac_all_desktops("Muninn", True)

    def restore(self) -> dict[str, Any]:
        """The raven was clicked (or the window was brought back some other way): the window returns, the raven leaves."""
        with self._lock:
            if self._home is not None and self.pinned:  # come back next to the edge it was pinned to
                h = self._home
                try:
                    self.main.move(h.x, h.y)
                except Exception:  # noqa: BLE001
                    pass
            self.main.show()
            try:
                self.main.restore()
            except Exception:  # noqa: BLE001
                pass
            self.mascot.hide()
            self.pinned, self._hidden_by_pin, self._prompted = None, False, None
            self._remember()
        return {"ok": True, **self.state()}

    def on_minimized(self) -> None:
        """The window was minimized: the raven chills where it was last pinned (or at the right edge) until it is clicked."""
        with self._lock:
            if self.mascot is None or self._hidden_by_pin:
                return
            b = self._main_box()
            x, y = self.pin_at or self.mascot_spot(self.edge, b)
            self._show_mascot(x, y)

    def minimize(self) -> dict[str, Any]:
        with self._lock:
            self.main.minimize()
            self.on_minimized()
        return {"ok": True, **self.state()}

    def on_restored(self) -> None:
        with self._lock:
            if not self._hidden_by_pin:
                self.mascot.hide()

    def resize_mascot(self, w: int, h: int) -> None:
        """The raven's window grows to fit what it says and shrinks back afterwards; the bolt stays where it is, the bubble opens toward the middle of the screen."""
        with self._lock:
            ow, oh = int(self.mascot.width), int(self.mascot.height)
            x, y = int(self.mascot.x), int(self.mascot.y)
            w, h = max(MASCOT_W, min(int(w), 300)), max(MASCOT_H, min(int(h), 220))
            scr, (il, it, ir, ib) = self._screen_of(x + ow // 2, y + oh // 2)
            right = scr.x + scr.w - ir
            if self.edge == "right" or x + w > right + 8:
                nx = x + ow - w
            else:
                nx = x
            ny = max(scr.y + it, min(y + (oh - h) // 2, scr.y + scr.h - ib - h))
            self.mascot.resize(w, h)
            self.mascot.move(nx, ny)

    def move_mascot(self, dx: int, dy: int) -> None:
        x, y = int(self.mascot.x) + dx, int(self.mascot.y) + dy
        self.mascot.move(x, y)

    def mascot_dropped(self) -> None:
        """After dragging the raven, it settles on the nearest edge of the screen it was dropped on and remembers that spot."""
        with self._lock:
            b = Box(int(self.mascot.x), int(self.mascot.y), int(self.mascot.width), int(self.mascot.height))
            edge = self.near_edge_for(b)
            self.edge = edge
            self.pin_at = self.mascot_spot(edge, b)
            self.mascot.move(*self.pin_at)
            if self._home is not None:
                self._home = Box(self._home.x, self._home.y, self._home.w, self._home.h)
            self.pinned = edge if self._hidden_by_pin else self.pinned
            self._remember()

    def near_edge_for(self, b: Box) -> str:
        scr, (il, it, ir, ib) = self._screen_of(b.x + b.w // 2, b.y + b.h // 2)
        left, top, right, bottom = scr.x + il, scr.y + it, scr.x + scr.w - ir, scr.y + scr.h - ib
        d = {"left": b.x - left, "right": right - (b.x + b.w), "top": b.y - top, "bottom": bottom - (b.y + b.h)}
        return min(d.items(), key=lambda kv: kv[1])[0]

    def snap(self, side: str) -> dict[str, Any]:
        """Split screen: Ragnarok takes the left or right half of the screen it is on, full height. The other half is free for another app."""
        with self._lock:
            b = self._main_box()
            scr, (il, it, ir, ib) = self._screen_of(b.x + b.w // 2, b.y + b.h // 2)
            left, top, right, bottom = scr.x + il, scr.y + it, scr.x + scr.w - ir, scr.y + scr.h - ib
            w = (right - left) // 2
            x = left if side == "left" else right - w
            self.main.resize(int(w), int(bottom - top))
            self.main.move(int(x), int(top))
            self._prompted = (side, 0.0)
        return {"ok": True, **self.state()}

    def all_desktops(self, on: bool) -> dict[str, Any]:
        self._all_desktops = bool(on)
        ok = _mac_all_desktops("Ragnarok", on) and _mac_all_desktops("Muninn", on)
        return {"ok": ok, **self.state()}

    def new_desktop(self) -> dict[str, Any]:
        """Move the window to the next desktop: it is allowed to follow the active desktop, the desktop is switched, and the window follows. Needs Accessibility."""
        if sys.platform != "darwin":
            return {"ok": False, "error": "E_UNSUPPORTED"}
        try:
            from AppKit import NSApp, NSWindowCollectionBehaviorMoveToActiveSpace
            from PyObjCTools import AppHelper

            def follow() -> None:
                for w in NSApp.windows():
                    if str(w.title()) == "Ragnarok":
                        w.setCollectionBehavior_(NSWindowCollectionBehaviorMoveToActiveSpace)

            AppHelper.callAfter(follow)
            self.main.hide()
            ok = _mac_next_desktop()
            threading.Timer(1.0, self.main.show).start()
        except Exception as exc:  # noqa: BLE001
            log("new_desktop_failed", err=type(exc).__name__)
            self.main.show()
            return {"ok": False, "error": "E_DESKTOP"}
        return {"ok": ok, "error": None if ok else "E_ACCESSIBILITY", **self.state()}

    def _remember(self) -> None:
        if self._store is not None:
            try:
                import json

                self._store.set("shell", json.dumps({"edge": self.edge, "pin_at": self.pin_at, "all_desktops": self._all_desktops}))
            except Exception:  # noqa: BLE001
                pass

    def load(self) -> None:
        if self._store is None:
            return
        try:
            import json

            d = json.loads(self._store.get("shell", "{}") or "{}")
            self.edge = d.get("edge", self.edge)
            self.pin_at = tuple(d["pin_at"]) if d.get("pin_at") else None  # type: ignore[assignment]
            self._all_desktops = bool(d.get("all_desktops"))
        except Exception:  # noqa: BLE001
            pass
