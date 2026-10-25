"""The scroll file format: a short YAML header, then plain instructions. Parsing is strict because a scroll is text that ends up in a model's prompt.

A scroll is a SPECIALIST, not a program: it makes the local model behave as one specialist (an email triager, a meeting summarizer) and produces WORDS. It cannot run code, call a write tool, send, post or change anything.
Its `tools` are read-only lookups that CODE performs before the model is called (searching memory, reading the agenda), from a fixed allowlist; anything else makes the scroll invalid.
Anything that needs an action still goes through the normal planner, the policy engine and a click on an approval card.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from ragnarok.gateway_scan.scan import scan

READ_TOOLS = ("memory.search", "memory.recent", "calendar.agenda")  # the only lookups a scroll may ask for; all are read-only and done by code
PERMISSIONS = ("read",)  # a scroll never holds write permissions
MIN_MODELS = ("small", "medium", "large")
MAX_HEADER_CHARS = 2_000
MAX_BODY_CHARS = 6_000  # small models follow long instructions poorly: keep scrolls short and structured
NAME = re.compile(r"^[a-z][a-z0-9-]{2,40}$")


class ScrollError(ValueError):
    """The file is not a valid scroll. The message is for the person who wrote it."""


@dataclass
class Example:
    input: str
    contains: list[str] = field(default_factory=list)
    not_contains: list[str] = field(default_factory=list)
    max_words: int | None = None


@dataclass
class Scroll:
    name: str
    title: str
    description: str
    triggers: list[str]
    tools: list[str]
    permissions: list[str]
    min_model: str
    memory: str  # own | shared | both
    chain: list[str]
    examples: list[Example]
    body: str
    version: int = 1
    suspect: bool = False  # the injection scanner flagged the text: it needs a human to read it before it is trusted
    source: str = "user"  # builtin | user | draft
    sha: str = ""
    raw: str = ""

    def header_view(self) -> dict[str, Any]:
        """All the agent keeps in memory about a scroll until it is needed: about a hundred tokens."""
        return {"name": self.name, "title": self.title, "description": self.description, "triggers": self.triggers[:8], "min_model": self.min_model}

    def approx_tokens(self) -> tuple[int, int]:
        """(header tokens, full tokens) using a cheap estimate: what progressive loading saves."""
        head = len(" ".join([self.name, self.description, *self.triggers])) // 3 + 1
        return head, len(self.raw) // 3 + 1


def sha_of(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


def _strs(v: Any, field_name: str, limit: int, each: int = 160) -> list[str]:
    if v in (None, ""):
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ScrollError(f"`{field_name}` must be a list of text lines")
    if len(v) > limit:
        raise ScrollError(f"`{field_name}` has more than {limit} entries")
    return [x.strip()[:each] for x in v if x.strip()]


def parse(raw: str, *, source: str = "user") -> Scroll:
    text = raw.replace("\r\n", "\n").lstrip("﻿")
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        raise ScrollError("a scroll starts with a header between two lines of ---")
    head_txt, body = m.group(1), m.group(2).strip()
    if len(head_txt) > MAX_HEADER_CHARS:
        raise ScrollError(f"the header is longer than {MAX_HEADER_CHARS} characters")
    if len(body) > MAX_BODY_CHARS:
        raise ScrollError(f"the instructions are longer than {MAX_BODY_CHARS} characters: small models follow long instructions poorly, so keep it short")
    if not body:
        raise ScrollError("the instructions are empty")
    try:
        h = yaml.safe_load(head_txt) or {}
    except yaml.YAMLError as exc:
        raise ScrollError("the header is not valid YAML") from exc
    if not isinstance(h, dict):
        raise ScrollError("the header must be `key: value` lines")
    name = str(h.get("name", "")).strip()
    if not NAME.match(name):
        raise ScrollError("`name` must be 3 to 41 characters: lowercase letters, digits and dashes, starting with a letter")
    desc = str(h.get("description", "")).strip()
    if not 8 <= len(desc) <= 240:
        raise ScrollError("`description` must be one sentence of 8 to 240 characters")
    triggers = _strs(h.get("triggers"), "triggers", 12)
    if not triggers:
        raise ScrollError("`triggers` needs at least one example of what someone would say to use this scroll")
    tools = _strs(h.get("tools"), "tools", 4, 40)
    bad = [t for t in tools if t not in READ_TOOLS]
    if bad:
        raise ScrollError(f"`tools` may only list read-only lookups ({', '.join(READ_TOOLS)}); not allowed: {', '.join(bad)}")
    perms = _strs(h.get("permissions", ["read"]), "permissions", 4, 20) or ["read"]
    if any(p not in PERMISSIONS for p in perms):
        raise ScrollError("a scroll can only hold the `read` permission; anything that sends or changes something is done by the normal approval flow")
    mm = str(h.get("min_model", "small")).strip().lower()
    if mm not in MIN_MODELS:
        raise ScrollError(f"`min_model` must be one of {', '.join(MIN_MODELS)}")
    memory = str(h.get("memory", "shared")).strip().lower()
    if memory not in ("own", "shared", "both"):
        raise ScrollError("`memory` must be own, shared or both")
    chain = _strs(h.get("chain"), "chain", 3, 41)
    if any(not NAME.match(c) for c in chain) or name in chain:
        raise ScrollError("`chain` lists other scroll names (not itself)")
    examples: list[Example] = []
    for e in (h.get("examples") or [])[:8]:
        if not isinstance(e, dict) or not isinstance(e.get("input"), str):
            raise ScrollError("each example needs an `input` and what the answer should contain")
        examples.append(Example(e["input"][:1_500], _strs(e.get("contains"), "contains", 6, 80), _strs(e.get("not_contains"), "not_contains", 6, 80), int(e["max_words"]) if isinstance(e.get("max_words"), int) else None))
    try:
        version = int(h.get("version", 1))
    except (TypeError, ValueError):
        version = 1
    sc = scan(text, "ingest")
    return Scroll(name=name, title=str(h.get("title", name.replace("-", " ").title()))[:80], description=desc, triggers=triggers, tools=tools, permissions=perms, min_model=mm, memory=memory,
                  chain=chain, examples=examples, body=body, version=version, suspect=bool(sc.injection_suspected or sc.findings), source=source, sha=sha_of(text), raw=text)


def render(s: Scroll) -> str:
    """The canonical file for a scroll (used when the agent drafts one)."""
    head: dict[str, Any] = {"name": s.name, "title": s.title, "description": s.description, "version": s.version, "triggers": s.triggers, "tools": s.tools, "permissions": s.permissions,
                            "min_model": s.min_model, "memory": s.memory}
    if s.chain:
        head["chain"] = s.chain
    if s.examples:
        head["examples"] = [{k: v for k, v in {"input": e.input, "contains": e.contains, "not_contains": e.not_contains, "max_words": e.max_words}.items() if v not in (None, [], "")} for e in s.examples]
    return "---\n" + yaml.safe_dump(head, sort_keys=False, allow_unicode=True, width=100).strip() + "\n---\n" + s.body.strip() + "\n"
