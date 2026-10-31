"""Fernet encryption for every credential the connector holds.

Store access tokens, tenant webhook secrets and Cloudflare tokens are
ciphertext in the database; the key is ``ENCRYPTION_KEY``, an environment
variable, so a copy of the database alone opens nothing.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings


class CipherError(Exception):
    pass


def _fernet(settings: Settings) -> Fernet:
    if not settings.encryption_key:
        raise CipherError("ENCRYPTION_KEY is not set")
    try:
        return Fernet(settings.encryption_key.encode())
    except ValueError as exc:
        raise CipherError("ENCRYPTION_KEY is not a Fernet key") from exc


def encrypt(settings: Settings, plaintext: str) -> str:
    return _fernet(settings).encrypt(plaintext.encode()).decode()


def decrypt(settings: Settings, ciphertext: str) -> str:
    try:
        return _fernet(settings).decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise CipherError("stored credential cannot be decrypted") from exc
