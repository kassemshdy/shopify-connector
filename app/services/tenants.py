"""Registering websites. Used by the operator CLI and page, never by tenants."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.crypto import encrypt
from app.errors import Unauthorized
from app.models import Tenant
from app.security import hash_key, new_api_key, new_webhook_secret


def create(
    db: Session, settings: Settings, *, slug: str, name: str, return_origins: list[str],
    webhook_url: str | None = None, subdomain_base: str | None = None,
) -> tuple[Tenant, str, str]:
    key, secret = new_api_key(), new_webhook_secret()
    tenant = Tenant(
        slug=slug, name=name, api_key_prefix=key[:14], api_key_hash=hash_key(key),
        webhook_secret_enc=encrypt(settings, secret), webhook_url=webhook_url,
        return_origins=" ".join(o.rstrip("/") for o in return_origins),
        subdomain_base=subdomain_base,
    )
    db.add(tenant)
    db.commit()
    return tenant, key, secret


def rotate_key(db: Session, tenant: Tenant) -> str:
    key = new_api_key()
    tenant.api_key_prefix = key[:14]
    tenant.api_key_hash = hash_key(key)
    db.commit()
    return key


def rotate_secret(db: Session, settings: Settings, tenant: Tenant) -> str:
    secret = new_webhook_secret()
    tenant.webhook_secret_enc = encrypt(settings, secret)
    db.commit()
    return secret


def set_cloudflare(
    db: Session, settings: Settings, tenant: Tenant, *, zone_id: str | None, token: str | None
) -> None:
    """Set the zone; the token is replaced only when one is given (it is never shown)."""
    tenant.cloudflare_zone_id = zone_id or None
    if token:
        tenant.cloudflare_token_enc = encrypt(settings, token)
    if not tenant.cloudflare_zone_id:
        tenant.cloudflare_token_enc = None
    db.commit()


def authenticate(db: Session, key: str) -> Tenant:
    tenant = db.scalars(select(Tenant).where(Tenant.api_key_hash == hash_key(key))).first()
    if tenant is None or not tenant.active:
        raise Unauthorized("bad_api_key", "Missing or invalid API key.")
    return tenant
