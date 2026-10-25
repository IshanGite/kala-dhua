"""One-time Google sign-in for a desktop app: loopback redirect with PKCE. The refresh token goes to the OS vault, nothing touches disk.

Official-docs notes (verify live): authorisation endpoint https://accounts.google.com/o/oauth2/v2/auth with access_type=offline and prompt=consent so a
refresh token is returned; redirect http://127.0.0.1:<port> is allowed for Desktop-app clients; code_challenge_method=S256.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from ragnarok.connectors.google import READ_SCOPES, TOKEN_URL, WRITE_SCOPES
from ragnarok.platform.secrets import SecretStore, token_name

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
PAGE = b"<html><body style='font-family:sans-serif'><h3>Ragnarok is connected.</h3>You can close this tab.</body></html>"


def load_client_file(path: Path) -> tuple[str, str]:
    d = json.loads(path.read_text(encoding="utf-8"))
    c = d.get("installed") or d.get("web") or {}
    if not c.get("client_id") or not c.get("client_secret"):
        raise ValueError("this does not look like a Desktop-app OAuth client file")
    return str(c["client_id"]), str(c["client_secret"])


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def build_auth_url(client_id: str, redirect: str, challenge: str, state: str, scopes: list[str]) -> str:
    q = {"client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": " ".join(scopes), "access_type": "offline", "prompt": "consent",
         "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    return f"{AUTH_URL}?{urlencode(q)}"


def _wait_for_code(server: HTTPServer, state: str, timeout: float) -> str:
    box: dict[str, Any] = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            q = parse_qs(urlparse(self.path).query)
            if q.get("state", [""])[0] == state and "code" in q:
                box["code"] = q["code"][0]
            elif "error" in q:
                box["error"] = q["error"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *a: Any) -> None:  # never log the code
            pass

    server.RequestHandlerClass = H
    server.timeout = timeout
    t = threading.Thread(target=server.handle_request)
    t.start()
    t.join(timeout + 2)
    if "code" not in box:
        raise TimeoutError(box.get("error", "no sign-in received"))
    return str(box["code"])


def sign_in(client_id: str, client_secret: str, vault: SecretStore, *, services: list[str] | None = None, timeout: float = 300, open_browser: bool = True, write: bool = False) -> None:
    services = services or list(READ_SCOPES)
    scopes = [x for s in services for x in WRITE_SCOPES[s]] if write else [READ_SCOPES[s] for s in services]
    server = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(16)
    url = build_auth_url(client_id, redirect, challenge, state, scopes)
    print("Opening your browser to sign in. If it does not open, visit:\n" + url)
    if open_browser:
        webbrowser.open_new_tab(url)
    code = _wait_for_code(server, state, timeout)
    server.server_close()
    r = httpx.post(TOKEN_URL, data={"client_id": client_id, "client_secret": client_secret, "code": code, "code_verifier": verifier, "redirect_uri": redirect, "grant_type": "authorization_code"}, timeout=20)
    d = r.json()
    if "refresh_token" not in d:
        raise RuntimeError(f"google did not return a refresh token: {d.get('error', r.status_code)}")
    vault.set(token_name("google", "write" if write else "read"), json.dumps({"client_id": client_id, "client_secret": client_secret, "refresh_token": d["refresh_token"], "services": services}))
