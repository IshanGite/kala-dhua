"""Identifiers and hashing helpers. Uses hashlib only (never Python's salted hash())."""

from __future__ import annotations

import hashlib
import random
import uuid

_rng: random.Random | None = None


def seed_ids(seed: int | None) -> None:
    """Make new_id() deterministic (tests, synthetic persona). None restores random UUIDs."""
    global _rng
    _rng = random.Random(seed) if seed is not None else None


def new_id() -> str:
    if _rng is not None:
        return uuid.UUID(int=_rng.getrandbits(128), version=4).hex
    return uuid.uuid4().hex


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def stable_key(*parts: str) -> str:
    """Idempotency key from parts, stable across processes and operating systems."""
    return sha256_hex("\x1f".join(parts))[:32]
