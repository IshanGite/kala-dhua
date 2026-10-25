"""Local API protection (SEC-11, SEC-12, SEC-20 groundwork).

* Bind to loopback only; reject any request whose Host or Origin is not local (blocks DNS rebinding and
  browser hijack of a local service, the "ClawJacked" class of attack).
* A random per-session token is required for every route except /healthz; failed attempts are rate limited.
* No CORS headers are ever sent, and framing is forbidden so an approval page cannot be clickjacked.
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

HEADER = "x-ragnarok-token"
MAX_FAILURES = 5
WINDOW_S = 60.0


class TokenGuard:
    def __init__(self, token: str | None = None) -> None:
        self.token = token or secrets.token_urlsafe(32)
        self._failures: deque[float] = deque()

    def check(self, presented: str | None) -> tuple[bool, int]:
        """Returns (ok, status). 401 for a bad token, 429 once the failure budget is spent."""
        now = time.monotonic()
        while self._failures and now - self._failures[0] > WINDOW_S:
            self._failures.popleft()
        if len(self._failures) >= MAX_FAILURES:
            return False, 429
        if presented and hmac.compare_digest(presented, self.token):
            return True, 200
        self._failures.append(now)
        return False, 401


def allowed_hosts(port: int) -> set[str]:
    names = ("127.0.0.1", "localhost", "[::1]")
    return {f"{n}:{port}" for n in names}


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, port: int, guard: TokenGuard, public_paths: frozenset[str] = frozenset({"/healthz"})) -> None:
        super().__init__(app)
        self.hosts = allowed_hosts(port)
        self.origins = {f"http://{h}" for h in self.hosts}
        self.guard = guard
        self.public = public_paths

    async def dispatch(self, request: Request, call_next) -> Response:
        host = request.headers.get("host", "")
        if host not in self.hosts:
            return JSONResponse({"error": "E_HOST"}, status_code=403)
        origin = request.headers.get("origin")
        if origin is not None and origin not in self.origins:
            return JSONResponse({"error": "E_ORIGIN"}, status_code=403)
        if request.url.path not in self.public:
            ok, status = self.guard.check(request.headers.get(HEADER))
            if not ok:
                return JSONResponse({"error": "E_AUTH"}, status_code=status)
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        response.headers["Cache-Control"] = "no-store"
        return response
