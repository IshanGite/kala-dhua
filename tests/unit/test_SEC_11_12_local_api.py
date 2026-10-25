"""SEC-11 (loopback only) and SEC-12 (foreign Host / Origin rejected, token required, no framing)."""

from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from ragnarok.daemon.api import DEFAULT_HOST, DEFAULT_PORT, create_app
from ragnarok.daemon.auth import HEADER, TokenGuard

SRC = Path(__file__).resolve().parents[2] / "src" / "ragnarok"


def _client(guard: TokenGuard):
    return TestClient(create_app(guard), base_url=f"http://127.0.0.1:{DEFAULT_PORT}")


def test_SEC_11_loopback_only_default_and_no_wildcard_bind_in_source():
    assert DEFAULT_HOST == "127.0.0.1"
    for p in SRC.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert '"0.0.0.0"' not in text and "'0.0.0.0'" not in text, f"wildcard bind in {p.name}"


def test_SEC_12_foreign_host_rejected():
    g = TokenGuard("tok")
    r = _client(g).get("/healthz", headers={"Host": "evil.example.com"})
    assert r.status_code == 403


def test_SEC_12_dns_rebinding_host_with_wrong_port_rejected():
    g = TokenGuard("tok")
    r = _client(g).get("/healthz", headers={"Host": "127.0.0.1:9999"})
    assert r.status_code == 403


def test_SEC_12_foreign_origin_rejected_even_with_valid_token():
    g = TokenGuard("tok")
    r = _client(g).get("/status", headers={HEADER: "tok", "Origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_SEC_12_token_required_for_everything_but_healthz():
    g = TokenGuard("tok")
    c = _client(g)
    assert c.get("/healthz").status_code == 200
    assert c.get("/status").status_code == 401
    assert c.get("/status", headers={HEADER: "wrong"}).status_code == 401
    assert c.get("/status", headers={HEADER: "tok"}).status_code == 200


def test_SEC_12_failed_token_attempts_are_rate_limited():
    g = TokenGuard("tok")
    c = _client(g)
    for _ in range(5):
        assert c.get("/status", headers={HEADER: "nope"}).status_code == 401
    assert c.get("/status", headers={HEADER: "nope"}).status_code == 429
    # even the right token is refused while the window is saturated
    assert c.get("/status", headers={HEADER: "tok"}).status_code == 429


def test_SEC_12_no_cors_and_framing_forbidden():
    g = TokenGuard("tok")
    r = _client(g).get("/healthz")
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
