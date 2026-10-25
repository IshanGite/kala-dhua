"""Structured errors. Every failure carries a code and a retryable flag, never message content."""

from __future__ import annotations


class RagnarokError(Exception):
    code = "E_GENERIC"
    retryable = False

    def __init__(self, detail: str = "", *, code: str | None = None, retryable: bool | None = None) -> None:
        super().__init__(detail or self.code)
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.detail = detail


class ConfigError(RagnarokError):
    code = "E_CONFIG"


class StoreError(RagnarokError):
    code = "E_STORE"


class WrongKeyError(StoreError):
    code = "E_WRONG_KEY"


class PolicyDenied(RagnarokError):
    code = "E_POLICY_DENIED"


class ModelError(RagnarokError):
    code = "E_MODEL"
    retryable = True


class ModelNotAllowed(RagnarokError):
    code = "E_MODEL_NOT_ALLOWED"


class NetworkGuardError(RagnarokError):
    code = "E_NETWORK_GUARD"


class SandboxUnavailable(RagnarokError):
    code = "E_SANDBOX_UNAVAILABLE"
