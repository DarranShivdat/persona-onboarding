"""App-level encryption for Google refresh tokens at rest (AES-256-GCM).

Key: PERSONA_TOKEN_ENCRYPT_KEY = urlsafe base64 of 32 random bytes. Generate with
    python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
Ciphertext layout: b"v1" | 12-byte nonce | AES-GCM(ciphertext+tag). The session id is bound
as associated data, so a row's ciphertext can't be replayed onto another session.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
from typing import Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENV_KEY = "PERSONA_TOKEN_ENCRYPT_KEY"
_VERSION = b"v1"
_NONCE = 12


class TokenKeyError(RuntimeError):
    """Missing/invalid key, or ciphertext that doesn't decrypt under it (message never has token data)."""


def generate_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


class TokenCipher:
    def __init__(self, key: str):
        try:
            raw = base64.urlsafe_b64decode(key.encode())
        except (binascii.Error, ValueError):
            raise TokenKeyError(f"{ENV_KEY} is not urlsafe base64") from None
        if len(raw) != 32:
            raise TokenKeyError(f"{ENV_KEY} must decode to 32 bytes")
        self._aead = AESGCM(raw)
        # Non-secret fingerprint so rows record which key encrypted them (rotation aid).
        self.key_id = hashlib.sha256(b"persona-token-key:" + raw).hexdigest()[:12]

    def __repr__(self) -> str:
        return f"TokenCipher(key_id={self.key_id})"

    @classmethod
    def from_env(cls) -> Optional["TokenCipher"]:
        key = os.environ.get(ENV_KEY)
        return cls(key) if key else None

    def encrypt(self, plaintext: str, *, aad: str) -> bytes:
        nonce = os.urandom(_NONCE)
        return _VERSION + nonce + self._aead.encrypt(nonce, plaintext.encode(), aad.encode())

    def decrypt(self, blob: bytes, *, aad: str) -> str:
        blob = bytes(blob)
        if not blob.startswith(_VERSION) or len(blob) <= len(_VERSION) + _NONCE:
            raise TokenKeyError("unrecognized ciphertext format")
        nonce, ct = blob[2:2 + _NONCE], blob[2 + _NONCE:]
        try:
            return self._aead.decrypt(nonce, ct, aad.encode()).decode()
        except InvalidTag:
            raise TokenKeyError("ciphertext does not decrypt under this key/session") from None
