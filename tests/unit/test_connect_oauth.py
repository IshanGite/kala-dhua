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
