"""Tenant API keys and the signatures on webhooks we send.

A tenant key is shown once and stored as its SHA-256: it is 32 random bytes,
not something a person chose, so there is nothing to slow-hash, and the
lookup runs on every request.

Outgoing webhooks carry ``X-Connector-Signature: t=<unix>,v1=<hex>`` where
``v1 = HMAC-SHA256(secret, f"{t}.{body}")``. The timestamp lets a receiver
refuse replays; docs/API.md has verification examples.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

KEY_PREFIX = "sc_live_"


def new_api_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_webhook_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def sign(secret: str, body: bytes, timestamp: int | None = None) -> str:
    t = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify(secret: str, body: bytes, header: str, tolerance: int = 300) -> bool:
    """Reference implementation of what a tenant should do with our signature."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        t = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs(time.time() - t) > tolerance:
        return False
    expected = sign(secret, body, t).split("v1=", 1)[1]
    return hmac.compare_digest(expected, parts.get("v1", ""))
