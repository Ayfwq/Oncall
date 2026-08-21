from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretBox:
    def __init__(self, master_key: str):
        if master_key:
            try:
                key = base64.urlsafe_b64decode(master_key + '=' * (-len(master_key) % 4))
            except Exception:
                key = hashlib.sha256(master_key.encode()).digest()
        else:
            key = hashlib.sha256(b'oncall-development-key-change-me').digest()
        if len(key) not in (16, 24, 32):
            key = hashlib.sha256(key).digest()
        self._aes = AESGCM(key)

    def encrypt(self, value: str) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._aes.encrypt(nonce, value.encode(), None)

    def decrypt(self, payload: bytes | None) -> str:
        if not payload:
            return ''
        nonce, ciphertext = payload[:12], payload[12:]
        return self._aes.decrypt(nonce, ciphertext, None).decode()
