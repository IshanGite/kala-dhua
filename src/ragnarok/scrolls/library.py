"""The scroll library: only the headers stay in memory; a scroll's full text is loaded when it is chosen (progressive loading).

Trust: a scroll shipped with the app is pinned to the version in the app. Any other scroll is untrusted until a human approves THIS exact text (its hash); editing it, or a model drafting a revision, puts it back to
'pending'. Approval is a click in the window (never a typed word, never something the model can do). A scroll the injection scanner flagged is shown with a warning before it can be approved.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ragnarok.core.clock import get_clock
from ragnarok.core.logging import log
from ragnarok.llm_gateway.client import Gateway, Priority
from ragnarok.scrolls.format import Scroll, ScrollError, parse

ROUTE_MIN = 0.62  # cosine similarity to the closest trigger sentence needed before a scroll takes a message
ROUTE_MARGIN = 0.04
ROUTE_CLEAR = (0.56, 0.10)  # or a lower similarity that still leads the runner-up by a wide gap: one scroll is plainly the nearest
ROUTE_STRONG = 0.75  # a message this close to a scroll's own example takes it even when it also looks like a summary or a lookup  # and how far ahead of the runner-up it must be
EXPLICIT = re.compile(r"\b(?:use|run|apply|with)\s+(?:the\s+)?([a-z][a-z0-9-]{2,40})\s+(?:scroll|skill)\b|\bscroll\s+([a-z][a-z0-9-]{2,40})\b", re.I)


@dataclass
class Route:
    name: str
    score: float
    runner_up: float
    explicit: bool = False
    strong: bool = False


class ScrollLibrary:
    def __init__(self, builtin_dir: Path, user_dir: Path, gateway: Gateway | None = None) -> None:
        self.builtin_dir, self.user_dir, self.draft_dir = builtin_dir, user_dir, user_dir / "drafts"
        self.state_path = user_dir / "state.json"
        self._gw = gateway
        self._scrolls: dict[str, Scroll] = {}
        self.errors: dict[str, str] = {}  # file name -> why it is not a valid scroll
        self._vecs: dict[str, tuple[str, np.ndarray]] = {}  # name -> (sha, matrix of [description, triggers...])
        self._lock = threading.RLock()
        self.stats = {"header_tokens": 0, "full_tokens_loaded": 0, "loads": 0}
        user_dir.mkdir(parents=True, exist_ok=True)
        self.draft_dir.mkdir(parents=True, exist_ok=True)
        self.reload()

    # ------------------------------------------------------------------ state file
    def _state(self) -> dict[str, Any]:
        try:
            d = json.loads(self.state_path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self, d: dict[str, Any]) -> None:
        fd, tmp = tempfile.mkstemp(dir=str(self.user_dir), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, self.state_path)

    # ------------------------------------------------------------------ loading
    def reload(self) -> None:
        with self._lock:
            found: dict[str, Scroll] = {}
            self.errors = {}
            for src, d in (("builtin", self.builtin_dir), ("user", self.user_dir)):
                for p in sorted(d.glob("*.md")) if d.exists() else []:
                    try:
                        s = parse(p.read_text(encoding="utf-8"), source=src)
                    except (ScrollError, OSError, UnicodeDecodeError) as exc:
                        self.errors[p.name] = str(exc)[:200]
                        continue
                    if s.name != p.stem:
                        self.errors[p.name] = f"the file name must match the scroll name `{s.name}`"
                        continue
                    found[s.name] = s  # a user file with the same name replaces the shipped one, but then it needs approval
            self._scrolls = found
            self.stats["header_tokens"] = sum(s.approx_tokens()[0] for s in found.values())

    def status(self, s: Scroll) -> str:
        st = self._state()
        if s.name in st.get("disabled", []):
            return "disabled"
        if s.source == "builtin":
            return "approved"
        return "approved" if st.get("approved", {}).get(s.name) == s.sha else "pending"

    def usable(self) -> list[Scroll]:
        return [s for s in self._scrolls.values() if self.status(s) == "approved"]

    def list(self) -> list[dict[str, Any]]:
        st = self._state()
        out = []
        for s in sorted(self._scrolls.values(), key=lambda x: (x.source != "builtin", x.name)):
            h, f = s.approx_tokens()
            out.append({"name": s.name, "title": s.title, "description": s.description, "source": s.source, "status": self.status(s), "suspect": s.suspect, "min_model": s.min_model, "tools": s.tools,
                        "chain": s.chain, "memory": s.memory, "examples": len(s.examples), "header_tokens": h, "full_tokens": f, "sha": s.sha, "version": s.version, "tests": st.get("tests", {}).get(s.name),
                        "usage": st.get("usage", {}).get(s.name, {"runs": 0})})
        return out

    def get(self, name: str) -> Scroll | None:
        """The full text, loaded because it was chosen: counted, so the saving over one big prompt can be reported."""
        s = self._scrolls.get(name)
        if s is not None:
            self.stats["full_tokens_loaded"] += s.approx_tokens()[1]
            self.stats["loads"] += 1
        return s

    def peek(self, name: str) -> Scroll | None:
        return self._scrolls.get(name)

    # ------------------------------------------------------------------ approval
    def approve(self, name: str, sha: str) -> bool:
        with self._lock:
            s = self._scrolls.get(name)
            if s is None or s.sha != sha:
                return False  # the file changed since the person looked at it
            st = self._state()
            st.setdefault("approved", {})[name] = sha
            st["disabled"] = [d for d in st.get("disabled", []) if d != name]
            self._save_state(st)
            log("scroll_approved", scroll=name)
            return True

    def set_enabled(self, name: str, on: bool) -> bool:
        with self._lock:
            if name not in self._scrolls:
                return False
            st = self._state()
            dis = [d for d in st.get("disabled", []) if d != name]
            st["disabled"] = dis if on else [*dis, name]
            self._save_state(st)
            return True

    def record_tests(self, name: str, passed: int, total: int, model: str, detail: list[dict[str, Any]] | None = None) -> None:
        with self._lock:
            st = self._state()
            st.setdefault("tests", {})[name] = {"passed": passed, "total": total, "model": model, "at": get_clock().now_ms(), "detail": (detail or [])[:8]}
            self._save_state(st)

    def record_use(self, name: str, ok: bool) -> None:
        with self._lock:
            st = self._state()
            u = st.setdefault("usage", {}).setdefault(name, {"runs": 0, "ok": 0})
            u["runs"] += 1
            u["ok"] += 1 if ok else 0
            self._save_state(st)

    # ------------------------------------------------------------------ drafts and diffs
    @staticmethod
    def diff(old: str, new: str) -> str:
        return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), "current", "proposed", n=2))

    def save_draft(self, raw: str, reason: str) -> dict[str, Any]:
        s = parse(raw, source="draft")  # a draft must already be a valid scroll
        did = f"{s.name}-{get_clock().now_ms() // 1000}"
        (self.draft_dir / f"{did}.md").write_text(raw, encoding="utf-8")
        (self.draft_dir / f"{did}.json").write_text(json.dumps({"reason": reason[:300], "name": s.name, "at": get_clock().now_ms()}), encoding="utf-8")
        cur = self._scrolls.get(s.name)
        return {"id": did, "name": s.name, "reason": reason, "diff": self.diff(cur.raw, s.raw) if cur else "", "new": cur is None, "suspect": s.suspect}

    def drafts(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self.draft_dir.glob("*.md")):
            try:
                s = parse(p.read_text(encoding="utf-8"), source="draft")
                meta = json.loads((self.draft_dir / f"{p.stem}.json").read_text(encoding="utf-8")) if (self.draft_dir / f"{p.stem}.json").exists() else {}
            except (ScrollError, OSError, ValueError):
                continue
            cur = self._scrolls.get(s.name)
            out.append({"id": p.stem, "name": s.name, "title": s.title, "description": s.description, "reason": meta.get("reason", ""), "new": cur is None, "suspect": s.suspect,
                        "diff": self.diff(cur.raw, s.raw) if cur else "", "text": s.raw, "at": meta.get("at", 0)})
        return out

    def approve_draft(self, draft_id: str) -> str | None:
        """A human clicked Approve on a draft: it becomes the user's scroll, pinned to exactly the text they saw."""
        p = self.draft_dir / f"{Path(draft_id).name}.md"
        if not p.exists():
            return None
        raw = p.read_text(encoding="utf-8")
        s = parse(raw, source="user")
        (self.user_dir / f"{s.name}.md").write_text(raw, encoding="utf-8")
        p.unlink(missing_ok=True)
        (self.draft_dir / f"{p.stem}.json").unlink(missing_ok=True)
        self.reload()
        self.approve(s.name, s.sha)
        return s.name

    def reject_draft(self, draft_id: str) -> bool:
        p = self.draft_dir / f"{Path(draft_id).name}.md"
        ok = p.exists()
        p.unlink(missing_ok=True)
        (self.draft_dir / f"{p.stem}.json").unlink(missing_ok=True)
        return ok

    def write_user_scroll(self, raw: str) -> Scroll:
        """Save text as the user's scroll (it stays pending until approved)."""
        s = parse(raw, source="user")
        (self.user_dir / f"{s.name}.md").write_text(raw, encoding="utf-8")
        self.reload()
        return self._scrolls[s.name]

    # ------------------------------------------------------------------ routing
    async def _matrix(self, s: Scroll) -> np.ndarray | None:
        cached = self._vecs.get(s.name)
        if cached and cached[0] == s.sha:
            return cached[1]
        if self._gw is None:
            return None
        texts = [s.description, *s.triggers]
        v = await self._gw.embed(texts, deadline=get_clock().monotonic() + 15, priority=Priority.INTERACTIVE)
        v = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
        self._vecs[s.name] = (s.sha, v)
        return v

    async def route(self, request: str) -> Route | None:
        """The scroll that fits a message, or None. Only approved, enabled scrolls take part; 'use the X scroll' names one directly."""
        usable = {s.name: s for s in self.usable()}
        if not usable:
            return None
        m = EXPLICIT.search(request)
        if m:
            nm = (m.group(1) or m.group(2) or "").lower()
            if nm in usable:
                return Route(nm, 1.0, 0.0, True)
        if self._gw is None:
            return None
        try:
            head = re.split(r"[:\n]", request.strip(), maxsplit=1)[0][:200]  # 'summarize these meeting notes: <pasted notes>': the ask is the part before the paste
            cands = [request[:600]] + ([head] if head and head != request.strip()[:600] and len(head) >= 8 else [])
            qs = await self._gw.embed(cands, deadline=get_clock().monotonic() + 5, priority=Priority.INTERACTIVE)
            qs = qs / np.maximum(np.linalg.norm(qs, axis=1, keepdims=True), 1e-9)
            scores: list[tuple[float, str]] = []
            for s in usable.values():
                mat = await self._matrix(s)
                if mat is not None and mat.shape[1] == qs.shape[1]:
                    scores.append((float(np.max(mat @ qs.T)), s.name))
        except Exception as exc:  # noqa: BLE001 - routing is an optimisation, never a failure
            log("scroll_route_failed", err=type(exc).__name__)
            return None
        scores.sort(reverse=True)
        if not scores:
            return None
        second = scores[1][0] if len(scores) > 1 else 0.0
        top = scores[0][0]
        clear = top >= ROUTE_CLEAR[0] and top - second >= ROUTE_CLEAR[1]
        if top < ROUTE_MIN and not clear:
            return None
        if top - second < ROUTE_MARGIN and second >= ROUTE_MIN:
            return None  # two scrolls fit equally well: better to answer normally than to pick one at random
        return Route(scores[0][1], scores[0][0], second, strong=scores[0][0] >= ROUTE_STRONG)
