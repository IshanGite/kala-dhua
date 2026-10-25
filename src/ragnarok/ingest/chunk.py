"""Structure-aware chunking with heading paths and character offsets (so citations can point at exact text)."""

from __future__ import annotations

import re

from ragnarok.memory.models import ChunkIn

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_CLAUSE = re.compile(r"^((?:\d+\.)+\d*|Section \d+(?:\.\d+)*|Article [IVXLC\d]+|Clause \d+(?:\.\d+)*)[\s:.-]+\S")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def _is_heading(line: str) -> tuple[int, str] | None:
    s = line.strip()
    if not s or len(s) > 90:
        return None
    m = _MD_HEADING.match(s)
    if m:
        return len(m.group(1)), m.group(2)
    if _CLAUSE.match(s) and not s.endswith((".", ",")):
        depth = s.split()[0].count(".") + 1 if s[0].isdigit() else 1
        return depth, s
    if s.isupper() and len(s.split()) <= 8 and any(c.isalpha() for c in s):
        return 1, s.title()
    return None


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    if len(paragraph) <= max_chars:
        return [paragraph]
    out, cur = [], ""
    for sent in _SENT.split(paragraph):
        if cur and len(cur) + len(sent) + 1 > max_chars:
            out.append(cur)
            cur = ""
        while len(sent) > max_chars:  # one unbroken run: hard split
            out.append(sent[:max_chars])
            sent = sent[max_chars:]
        cur = f"{cur} {sent}".strip()
    if cur:
        out.append(cur)
    return out


def chunk_text(text: str, *, max_chars: int = 1200, min_chars: int = 120, page: int | None = None) -> list[ChunkIn]:
    """Split on structure first (headings, clauses, paragraphs), then on sentences. Short tails merge into the previous chunk."""
    chunks: list[ChunkIn] = []
    stack: list[tuple[int, str]] = []
    buf: list[str] = []
    buf_start = 0
    cursor = 0

    def path() -> str | None:
        return " > ".join(t for _, t in stack) or None

    def flush(end: int) -> None:
        nonlocal buf, buf_start
        body = "\n\n".join(buf).strip()
        buf = []
        if not body:
            return
        for piece in _split_long(body, max_chars):
            chunks.append(ChunkIn(text=piece, heading_path=path(), page=page, char_start=buf_start, char_end=end))

    for block in re.split(r"\n\s*\n", text):
        start = text.find(block, cursor)
        start = cursor if start < 0 else start
        cursor = start + len(block)
        stripped = block.strip()
        if not stripped:
            continue
        first, _, rest = stripped.partition("\n")
        h = _is_heading(first)
        if h:
            flush(start)
            depth, title = h
            while stack and stack[-1][0] >= depth:
                stack.pop()
            stack.append((depth, title))
            buf_start = start
            if rest.strip():
                buf.append(rest.strip())
            continue
        if not buf:
            buf_start = start
        if sum(len(b) for b in buf) + len(stripped) > max_chars and buf:
            flush(start)
            buf_start = start
        buf.append(stripped)
    flush(len(text))

    merged: list[ChunkIn] = []
    for c in chunks:
        if merged and len(c.text) < min_chars and merged[-1].heading_path == c.heading_path and len(merged[-1].text) + len(c.text) + 2 <= max_chars:
            merged[-1] = ChunkIn(text=merged[-1].text + "\n\n" + c.text, heading_path=c.heading_path, page=c.page,
                                 char_start=merged[-1].char_start, char_end=c.char_end)
        else:
            merged.append(c)
    return merged


def embed_input(c: ChunkIn, title: str | None = None) -> str:
    """Text actually embedded: the heading path gives each chunk the context it lacks on its own."""
    head = " > ".join(x for x in (title, c.heading_path) if x)
    return f"{head}\n{c.text}" if head else c.text
