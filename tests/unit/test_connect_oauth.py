"""connect: Google client file parsing, PKCE correctness, auth URL, registry from vault."""

from __future__ import annotations

import base64
import hashlib
import json

import pytest
from typer.testing import CliRunner

from ragnarok.cli import app
from ragnarok.connectors.oauth_google import build_auth_url, load_client_file, pkce
from ragnarok.connectors.registry import build, connected
from ragnarok.platform.secrets import MemoryStore, token_name


def test_pkce_challenge_matches_verifier():
    v, c = pkce()
    assert c == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip("=") and 43 <= len(v) <= 128


def test_client_file_parsing(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"installed": {"client_id": "id", "client_secret": "sec"}}))
    assert load_client_file(f) == ("id", "sec")
    f.write_text(json.dumps({"nope": 1}))
    with pytest.raises(ValueError):
        load_client_file(f)


def test_auth_url_asks_for_offline_access_pkce_and_read_only_scopes():
    u = build_auth_url("cid", "http://127.0.0.1:5000", "chal", "st", ["https://www.googleapis.com/auth/gmail.readonly"])
    assert "access_type=offline" in u and "code_challenge_method=S256" in u and "gmail.readonly" in u and "client_secret" not in u


def test_registry_builds_from_vault_and_refuses_when_missing():
    v = MemoryStore()
    with pytest.raises(KeyError):
        build("slack", v)
    v.set(token_name("slack", "read"), "xoxb-x")
    v.set(token_name("google", "read"), json.dumps({"client_id": "a", "client_secret": "b", "refresh_token": "c"}))
    assert build("slack", v).name == "slack" and build("gmail", v).name == "gmail" and build("gmeet", v).name == "gmeet"
    assert set(connected(v)) == {"slack", "gmail", "gcal", "gdrive", "gmeet"}


def test_connect_command_is_listed():
    out = CliRunner().invoke(app, ["connect", "--help"]).output
    assert all(n in out for n in ("slack", "telegram", "discord", "google"))


def test_write_sign_in_asks_only_for_write_scopes_and_uses_a_separate_vault_name(monkeypatch):
    from ragnarok.connectors import oauth_google as og

    seen = {}
    monkeypatch.setattr(og, "_wait_for_code", lambda server, state, timeout: "code")
    monkeypatch.setattr(og.webbrowser, "open_new_tab", lambda url: seen.setdefault("url", url))

    class R:
        def json(self):
            return {"refresh_token": "rt"}

    monkeypatch.setattr(og.httpx, "post", lambda *a, **k: R())
    v = MemoryStore()
    og.sign_in("cid", "sec", v, services=["gmail", "calendar"], write=True)
    assert "gmail.send" in seen["url"] and "calendar.events" in seen["url"] and "readonly" not in seen["url"]
    assert v.get(token_name("google", "write")) and v.get(token_name("google", "read")) is None


def test_registry_builds_outlook_from_vault():
    v = MemoryStore()
    v.set(token_name("outlook", "read"), json.dumps({"client_id": "c", "refresh_token": "r"}))
    assert build("outlook", v).name == "outlook" and build("outlook_cal", v).name == "outlook_cal"
    assert set(connected(v)) == {"outlook", "outlook_cal"}
