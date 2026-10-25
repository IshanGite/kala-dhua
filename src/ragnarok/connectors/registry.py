"""Builds a connector from what is in the vault. The only place that reads connector tokens."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ragnarok.connectors.discord import Discord
from ragnarok.connectors.google import Calendar, Drive, Gmail, GoogleAuth, Meet
from ragnarok.connectors.local_files import LocalFiles
from ragnarok.connectors.outlook import MicrosoftAuth, OutlookCalendar, OutlookMail
from ragnarok.connectors.slack import Slack
from ragnarok.connectors.telegram import Telegram
from ragnarok.platform.secrets import SecretStore, token_name

NAMES = ("slack", "telegram", "discord", "gmail", "gcal", "gdrive", "gmeet", "outlook", "outlook_cal", "files")
MS = ("outlook", "outlook_cal")


def _key(name: str) -> str:
    return "google" if name in ("gmail", "gcal", "gdrive", "gmeet") else "outlook" if name in MS else name


def build(name: str, vault: SecretStore, **opts: Any) -> Any:
    if name == "files":
        return LocalFiles([Path(p) for p in opts["folders"]])
    raw = vault.get(token_name(_key(name), "read"))
    if raw is None:
        raise KeyError(f"{name} is not connected; run: ragnarok connect {name}")
    if name == "slack":
        return Slack(raw)
    if name == "telegram":
        return Telegram(raw)
    if name == "discord":
        return Discord(raw)
    if name in MS:
        m = json.loads(raw)
        return (OutlookMail if name == "outlook" else OutlookCalendar)(MicrosoftAuth(m["client_id"], m["refresh_token"]))
    g = json.loads(raw)
    auth = GoogleAuth(g["client_id"], g["client_secret"], g["refresh_token"])
    return {"gmail": Gmail, "gcal": Calendar, "gdrive": Drive, "gmeet": Meet}[name](auth)


def connected(vault: SecretStore) -> list[str]:
    out = []
    for n in NAMES:
        if n == "files":
            continue
        if vault.get(token_name(_key(n), "read")):
            out.append(n)
    return out
