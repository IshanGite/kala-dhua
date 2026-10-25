"""Key hierarchy (ARCHITECTURE 3, baseline "Key hierarchy").

One random 256-bit master key sits in the OS secret store. Every other key is derived from it with
HKDF and a distinct label, so no key is reused across purposes. Blob keys are random per file and are
stored wrapped, so deleting the row destroys the key (crypto-shredding).
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ragnarok.platform.secrets import SecretStore, default_store

MASTER_NAME = "master-key"
_SALT = b"ragnarok-v1"


class KeyHierarchy:
    def __init__(self, master: bytes) -> None:
        if len(master) != 32:
            raise ValueError("master key must be 32 bytes")
        self._master = master

    @classmethod
    def load_or_create(cls, store: SecretStore | None = None) -> KeyHierarchy:
        store = store or default_store()
        hexkey = store.get(MASTER_NAME)
        if hexkey is None:
            hexkey = os.urandom(32).hex()
            store.set(MASTER_NAME, hexkey)
        return cls(bytes.fromhex(hexkey))

    def derive(self, label: str, length: int = 32) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(), length=length, salt=_SALT, info=f"ragnarok/{label}".encode()
        ).derive(self._master)

    @property
    def db_key_sql(self) -> str:
        """SQLCipher raw key literal: x'<64 hex chars>' (no slow key stretching at boot)."""
        return "x'" + self.derive("db").hex() + "'"

    def hmac(self, label: str, data: bytes) -> bytes:
        return _hmac.new(self.derive(f"hmac/{label}"), data, hashlib.sha256).digest()

    def wrap(self, plaintext: bytes, aad: bytes = b"") -> bytes:
        nonce = os.urandom(12)
        return nonce + AESGCM(self.derive("wrap")).encrypt(nonce, plaintext, aad)

    def unwrap(self, blob: bytes, aad: bytes = b"") -> bytes:
        return AESGCM(self.derive("wrap")).decrypt(blob[:12], blob[12:], aad)

    def fingerprint(self) -> str:
        """Short, non-reversible id of the master key for logs and doctor output."""
        return hashlib.sha256(self._master).hexdigest()[:8]


def encrypt_bytes(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def decrypt_bytes(key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    return AESGCM(key).decrypt(blob[:12], blob[12:], aad)
