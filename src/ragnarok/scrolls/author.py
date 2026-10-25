"""Self-written scrolls: the agent notices requests you keep making and DRAFTS a scroll for them, and drafts a better version of a scroll whose own tests fail. Drafts are only ever proposals:
each is validated, tested against its own examples, shown to you as a diff, and used only after you click Approve. Nothing here changes a scroll that is in use."""

from __future__ import annotations

import json
import re
from typing import Any

import numpy as np
import yaml

from ragnarok.core.clock import get_clock
from ragnarok.core.errors import ModelError
from ragnarok.core.logging import log
from ragnarok.gateway_scan.scan import scan
from ragnarok.llm_gateway.client import Gateway, Priority
from ragnarok.scrolls.format import NAME, READ_TOOLS, ScrollError, parse, render, sha_of
from ragnarok.scrolls.library import ROUTE_MIN, ScrollLibrary
from ragnarok.scrolls.runner import ScrollRunner

CLUSTER_SIM = 0.80  # two requests this close mean the same kind of task
MIN_CLUSTER = 3
SCHEMA = {"type": "object", "properties": {
    "name": {"type": "string"}, "description": {"type": "string"}, "triggers": {"type": "array", "items": {"type": "string"}, "maxItems": 6}, "instructions": {"type": "string"},
    "tools": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
    "examples": {"type": "array", "maxItems": 2, "items": {"type": "object", "properties": {"input": {"type": "string"}, "contains": {"type": "array", "items": {"type": "string"}, "maxItems": 3}}, "required": ["input"]}}},
    "required": ["name", "description", "triggers", "instructions"]}
REVISE_SCHEMA = {"type": "object", "properties": {"instructions": {"type": "string"}}, "required": ["instructions"]}


async def mine_habits(gateway: Gateway, texts: list[str], library: ScrollLibrary) -> list[list[str]]:
    """Groups of near-identical kinds of request (at least three) that no existing scroll already handles."""
    msgs = [t.strip() for t in texts if 25 <= len(t.strip()) <= 600 and not t.strip().lower().startswith(("remember ", "forget "))][-200:]
    if len(msgs) < MIN_CLUSTER:
        return []
    try:
        v = await gateway.embed(msgs, deadline=get_clock().monotonic() + 40, priority=Priority.BACKGROUND)
    except ModelError:
        return []
    v = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
    clusters: list[list[int]] = []
    cents: list[np.ndarray] = []
    for i in range(len(msgs)):
        best, bi = -1.0, -1
        for j, c in enumerate(cents):
            s = float(v[i] @ c)
            if s > best:
                best, bi = s, j
        if best >= CLUSTER_SIM:
            clusters[bi].append(i)
            cents[bi] = v[clusters[bi]].mean(axis=0)
            cents[bi] /= max(float(np.linalg.norm(cents[bi])), 1e-9)
        else:
            clusters.append([i])
            cents.append(v[i].copy())
    out = []
    for c, cen in sorted(zip(clusters, cents, strict=True), key=lambda p: -len(p[0])):
        if len(c) < MIN_CLUSTER:
            continue
        covered = False
        for s in library.usable():  # a scroll that already fits these requests: nothing to draft
            m = await library._matrix(s)
            if m is not None and m.shape[1] == cen.shape[0] and float(np.max(m @ cen)) >= ROUTE_MIN - 0.04:
                covered = True
                break
        if not covered:
            out.append([msgs[i] for i in c][:6])
    return out


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:36].strip("-")
    return s if NAME.match(s) else ""


async def draft_from_cluster(gateway: Gateway, examples: list[str], library: ScrollLibrary) -> str | None:
    """Raw text of a valid draft scroll for a kind of request, or None. The model sees only the user's own requests."""
    listing = "\n".join(f"- {e[:200]}" for e in examples)
    try:
        out = await gateway.chat(role="arbiter", schema=SCHEMA, max_tokens=520, deadline=get_clock().monotonic() + 40, priority=Priority.BACKGROUND, seed=0, temperature=0.2,
                                 messages=[{"role": "system", "content": "You write a 'scroll': short instructions that make an assistant behave as one specialist for a recurring kind of request. Use only what the requests show. "
                                                                          "name: lowercase words joined by dashes. description: one sentence. triggers: 3 to 5 short example requests. instructions: under 600 characters, plain steps and the output format. "
                                                                          "tools: [] unless the specialist needs recent messages (memory.recent), memory (memory.search) or the agenda (calendar.agenda). examples: two inputs with words the answer must contain. JSON only."},
                                          {"role": "user", "content": f"The user keeps asking for things like this:\n{listing}"}])
    except ModelError:
        return None
    d = out.parsed or {}
    name = _slug(str(d.get("name", "")))
    if not name or library.peek(name) is not None:
        return None
    try:
        raw = "---\n" + yaml.safe_dump({"name": name, "title": name.replace("-", " ").title(), "description": str(d.get("description", ""))[:230], "version": 1,
                                         "triggers": [str(t)[:120] for t in d.get("triggers", [])][:6] or examples[:3],
                                         "tools": [t for t in d.get("tools", []) if t in READ_TOOLS][:2], "permissions": ["read"], "min_model": "small", "memory": "shared",
                                         "examples": [{"input": str(e.get("input", ""))[:300], "contains": [str(c)[:60] for c in e.get("contains", [])][:3]} for e in d.get("examples", [])[:2] if isinstance(e, dict) and e.get("input")]},
                                        sort_keys=False, allow_unicode=True, width=100).strip() + "\n---\n" + str(d.get("instructions", "")).strip()[:1500] + "\n"
        parse(raw, source="draft")
    except (ScrollError, ValueError):
        return None
    return raw


async def propose_new(gateway: Gateway, runner: ScrollRunner, texts: list[str], max_new: int = 2) -> list[dict[str, Any]]:
    """Draft scrolls for habits. Each draft is tested on its own examples first, and kept only if at least one passes (a draft that cannot pass its own test is not worth your time)."""
    lib, out = runner.lib, []
    for cluster in await mine_habits(gateway, texts, lib):
        if len(out) >= max_new:
            break
        raw = await draft_from_cluster(gateway, cluster, lib)
        if raw is None:
            continue
        s = parse(raw, source="draft")
        passed, total, _ = await runner.test_scroll(s) if s.examples else (0, 0, [])
        if total and passed == 0:
            log("scroll_draft_dropped", scroll=s.name, why="failed_own_tests")
            continue
        out.append(lib.save_draft(raw, f"You asked for things like this {len(cluster)} times recently: \"{cluster[0][:80]}\"" + (f". Its own tests: {passed} of {total} pass." if total else "")))
    return out


async def propose_revision(gateway: Gateway, runner: ScrollRunner, name: str) -> dict[str, Any] | None:
    """A better version of a scroll whose own tests fail: the model sees the failures, writes new instructions, and the new version is kept only if it passes MORE of the tests than the current one."""
    lib = runner.lib
    cur = lib.peek(name)
    if cur is None or not cur.examples:
        return None
    p0, t0, detail = await runner.test_scroll(cur)
    fails = [d for d in detail if not d["ok"]]
    if not fails:
        return None
    note = "\n".join(f"- input: {d['input']}\n  problem: {'; '.join(d['problems'])}\n  output was: {d['output'][:160]}" for d in fails[:3])
    try:
        out = await gateway.chat(role="arbiter", schema=REVISE_SCHEMA, max_tokens=520, deadline=get_clock().monotonic() + 40, priority=Priority.BACKGROUND, seed=1, temperature=0.3,
                                 messages=[{"role": "system", "content": "You improve the instructions of an assistant specialist. Keep the same job and format. Fix the problems listed. Under 700 characters. JSON {instructions}."},
                                           {"role": "user", "content": f"Current instructions:\n{cur.body}\n\nFailures when testing:\n{note}"}])
    except ModelError:
        return None
    body = str((out.parsed or {}).get("instructions", "")).strip()[:1800]
    if len(body) < 40 or scan(body, "prompt").injection_suspected:
        return None
    new_raw = render(cur.__class__(**{**cur.__dict__, "body": body, "version": cur.version + 1, "raw": "", "sha": ""}))
    try:
        new = parse(new_raw, source="draft")
    except ScrollError:
        return None
    p1, t1, _ = await runner.test_scroll(new)
    if p1 <= p0:
        log("scroll_revision_dropped", scroll=name, before=p0, after=p1)
        return None
    return lib.save_draft(new_raw, f"Its own tests: {p0} of {t0} passed; this revision passes {p1} of {t1}.")


def digest_of(texts: list[str]) -> str:
    return sha_of(json.dumps(texts)[:2000])
