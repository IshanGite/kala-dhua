"""Which apps are connected, answered from the real state by code, never by a model.

A language model asked "is WhatsApp connected?" will happily say yes. Ragnarok has no WhatsApp connector, so the honest answer is no, and a request to send a WhatsApp message must be refused rather than sent somewhere else.
"""

from __future__ import annotations

import re
from typing import Any

APPS = {  # what a person calls it -> the connector's name
    "gmail": "gmail", "google mail": "gmail", "e-mail": "gmail", "email": "gmail", "mail": "gmail",
    "google calendar": "gcal", "gcal": "gcal", "calendar": "gcal",
    "google drive": "gdrive", "gdrive": "gdrive", "google docs": "gdrive", "drive": "gdrive",
    "google meet": "gmeet", "meet": "gmeet",
    "slack": "slack", "discord": "discord", "telegram": "telegram",
    "local files": "files", "notifications": "notifications",
}
LABEL = {"gmail": "Gmail", "gcal": "Google Calendar", "gdrive": "Google Drive", "gmeet": "Google Meet", "slack": "Slack", "discord": "Discord", "telegram": "Telegram", "files": "Local files", "notifications": "Desktop notifications"}
CONNECT_HINT = {"gmail": "ragnarok connect google", "gcal": "ragnarok connect google", "gdrive": "ragnarok connect google", "gmeet": "ragnarok connect google", "slack": "ragnarok connect slack",
                "discord": "ragnarok connect discord", "telegram": "ragnarok connect telegram"}
UNSUPPORTED = {  # no connector exists for these in this build
    "whatsapp": "WhatsApp", "whats app": "WhatsApp", "imessage": "iMessage", "facebook": "Facebook", "instagram": "Instagram", "linkedin": "LinkedIn", "twitter": "Twitter", "snapchat": "Snapchat",
    "wechat": "WeChat", "viber": "Viber", "skype": "Skype", "microsoft teams": "Microsoft Teams", "outlook": "Outlook",
}
_APP_RX = re.compile(r"\b(" + "|".join(sorted((re.escape(k) for k in [*APPS, *UNSUPPORTED]), key=len, reverse=True)) + r")\b", re.I)
CONNECTION_ASK = re.compile(
    r"\b(?:is|are|am|was)\b\s+(?:\w+\s+){0,4}?(?:connected|linked|hooked up|logged in|signed in|integrated|set up)\b"
    r"|\b(?:connected|linked|integrated)\s+(?:to|with)\b|\bdo you (?:have|got) access to\b|\bhave you (?:got )?access to\b"
    r"|\b(?:which|what)\s+(?:apps|accounts|sources|services|integrations)\b|\bconnection status\b|\bwhat(?:'s| is) connected\b|\bwhat are you connected\b"
    r"|\bcan you (?:see|read|access|reach|open)\b.*\b(?:my\s+)?(?:\w+\s+)?(?:messages|chats?|inbox|account|notifications)\b", re.I)
ACTION_WORDS = re.compile(r"\b(send|message|text|tell|reply|respond|post|dm|ping|write|forward|share|call|notify|email|mail)\b", re.I)


def mentioned(text: str) -> tuple[list[str], list[str]]:
    """(connectors named, unsupported apps named), in order, without repeats."""
    ok: list[str] = []
    bad: list[str] = []
    for m in _APP_RX.finditer(text):
        w = m.group(1).lower()
        if w in UNSUPPORTED:
            if UNSUPPORTED[w] not in bad:
                bad.append(UNSUPPORTED[w])
        elif APPS[w] not in ok:
            ok.append(APPS[w])
    return ok, bad


def _unsupported_line(app: str) -> str:
    extra = " I can only show its notifications, read-only, if you add it under Sources > Notifications (apps to read)." if app == "WhatsApp" else ""
    if app == "Outlook":
        extra = " Outlook is not used in this build."
    return f"{app} is not connected, and Ragnarok has no {app} connector, so I cannot read or send {app} messages.{extra}"


def refuse_unsupported_send(text: str) -> str | None:
    """A request to DO something on an app that does not exist here is refused up front, so it can never be misread as another app."""
    ok, bad = mentioned(text)
    if not bad or not ACTION_WORDS.search(text) or CONNECTION_ASK.search(text):
        return None
    return "I did not send anything. " + _unsupported_line(bad[0])


def answer_connection(text: str, state: dict[str, Any] | None) -> str | None:
    """None when this is not a question about connections. `state` = {"read": [...], "write": [...], "health": {name: {"ok": bool|None}}}."""
    if not CONNECTION_ASK.search(text):
        return None
    ok, bad = mentioned(text)
    generic = bool(re.search(r"\b(?:which|what)\s+(?:apps|accounts|sources|services|integrations)\b|\bwhat(?:'s| is) connected\b|\bconnection status\b|\bwhat are you connected\b", text, re.I))
    if not ok and not bad and not generic:
        return None
    if state is None:
        return "I cannot check the connections right now."
    read, write, health = set(state.get("read", [])), set(state.get("write", [])), state.get("health", {})

    def one(n: str) -> str:
        if n in read or n in ("files", "notifications") and n in health:
            h = health.get(n, {}).get("ok")
            how = "reading" + (" and sending" if n in write else " only (sending is not set up)" if n in ("slack", "discord", "telegram", "gmail") else "")
            return f"{LABEL[n]}: connected, {how}" + ("" if h in (None, True) else " (the last check failed)")
        hint = CONNECT_HINT.get(n)
        return f"{LABEL[n]}: not connected" + (f" (run: {hint})" if hint else " (add it under Sources)")

    parts: list[str] = []
    for n in ok:
        parts.append(one(n))
    for b in bad:
        parts.append(_unsupported_line(b))
    if generic and not parts:
        names = ["gmail", "gcal", "gdrive", "gmeet", "slack", "discord", "telegram", "files", "notifications"]
        on = [LABEL[n] + (" (read and send)" if n in write else "") for n in names if (n in read or n in ("files", "notifications") and n in health)]
        off = [LABEL[n] for n in names if not (n in read or n in ("files", "notifications") and n in health)]
        parts.append("Connected: " + (", ".join(on) if on else "none") + ".")
        if off:
            parts.append("Not connected: " + ", ".join(off) + ".")
        parts.append("Apps without a connector in this build (for example WhatsApp) cannot be connected.")
    return "\n".join(parts)
