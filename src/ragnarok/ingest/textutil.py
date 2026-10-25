"""Mail and HTML text cleaning.

Prior work: html_to_text, strip_quoted and the greeting/sign-off handling are adapted from the groupify project
(github.com/abhi-dev99/groupify, coord/google/gmail.py), re-organised for Ragnarok.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "blockquote", "table"}
    HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0|opacity\s*:\s*0", re.I)

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.hidden_stack: list[str] = []
        self.saw_hidden = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        style = dict(attrs).get("style") or ""
        hidden = bool(self.HIDDEN_STYLE.search(style)) or "hidden" in dict(attrs)
        if tag in ("script", "style", "head", "noscript"):
            self.skip += 1
        elif hidden:  # hidden text is dropped (a person never sees it, a model would)
            self.hidden_stack.append(tag)
            self.saw_hidden = True
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head", "noscript"):
            self.skip = max(0, self.skip - 1)
        elif self.hidden_stack and self.hidden_stack[-1] == tag:
            self.hidden_stack.pop()
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip and not self.hidden_stack:
            self.out.append(data)


def html_to_text(html: str) -> tuple[str, bool]:
    """Returns (text, hidden_content_seen)."""
    p = _Text()
    p.feed(html or "")
    text = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t\xa0]+", " ", unescape("".join(p.out)))).strip()
    return text, p.saw_hidden


_CUTS = [
    re.compile(p, re.I | re.M | re.S)
    for p in (
        r"^\s*On .{5,250}?wrote:\s*$",
        r"^\s*-{2,}\s*(Original Message|Forwarded message)\s*-{2,}",
        r"^\s*From:\s.+\n\s*(Sent|Date):\s",
        r"^\s*(Sent from my |Get Outlook for )",
        r"^-- ?$",
    )
]
_SIGNOFF = re.compile(r"(?im)^\s*(?:thanks(?: a lot| so much)?|thank you|thx|many thanks|regards|warm regards|kind regards|best(?: regards| wishes)?|cheers|sincerely|rgds)\s*[,.!]?\s*$")
_GREETING = re.compile(r"(?i)\A\s*(?:hi|hello|hey|dear|namaste)\b[^\n]{0,40}?[,:!]?\s*\n+")


def strip_quoted(text: str) -> str:
    """Keep only what the person just wrote: drop the quoted thread, the signature and '> ' lines."""
    cut = len(text)
    for rx in _CUTS:
        m = rx.search(text)
        if m:
            cut = min(cut, m.start())
    lines = [ln for ln in text[:cut].splitlines() if not ln.lstrip().startswith(">")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def message_body(text: str) -> str:
    """A message without its wrapper: no quoted thread, signature, greeting or sign-off (the words themselves stay)."""
    body = strip_quoted(text)
    return _GREETING.sub("", _SIGNOFF.split(body, maxsplit=1)[0]).strip() or body
