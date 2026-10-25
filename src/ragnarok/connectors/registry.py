"""Builds a connector from what is in the vault. The only place that reads connector tokens."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ragnarok.connectors.discord import Discord
from ragnarok.connectors.google import Calendar, Drive, Gmail, GoogleAuth, Meet
from ragnarok.connectors.local_files import LocalFiles
from ragnarok.connectors.slack import Slack
from ragnarok.connectors.telegram import Telegram
from ragnarok.platform.secrets import SecretStore, token_name

NAMES = ("slack", "telegram", "discord", "gmail", "gcal", "gdrive", "gmeet", "files")


def build(name: str, vault: SecretStore, **opts: Any) -> Any:
    if name == "files":
        return LocalFiles([Path(p) for p in opts["folders"]])
    raw = vault.get(token_name("google" if name in ("gmail", "gcal", "gdrive", "gmeet") else name, "read"))
    if raw is None:
        raise KeyError(f"{name} is not connected; run: ragnarok connect {name}")
    if name == "slack":
        return Slack(raw)
    if name == "telegram":
        return Telegram(raw)
    if name == "discord":
        return Discord(raw)
    g = json.loads(raw)
    auth = GoogleAuth(g["client_id"], g["client_secret"], g["refresh_token"])
    return {"gmail": Gmail, "gcal": Calendar, "gdrive": Drive, "gmeet": Meet}[name](auth)


def connected(vault: SecretStore) -> list[str]:
    out = []
    for n in NAMES:
        if n == "files":
            continue
        key = "google" if n in ("gmail", "gcal", "gdrive", "gmeet") else n
        if vault.get(token_name(key, "read")):
            out.append(n)
    return out
