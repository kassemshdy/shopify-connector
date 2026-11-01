"""Register websites from the environment, for when no shell is at hand.

``BOOTSTRAP_TENANTS`` holds a JSON list; each entry creates the tenant if
its slug is new, or updates its settings if it exists:

    [{"slug": "janoubona", "name": "Janoubona",
      "origins": ["https://janoubona.net"],
      "webhook_url": "https://janoubona.net/api/integrations/connector/webhooks",
      "subdomain_base": "janoubona.net",
      "api_key": "sc_live_...", "webhook_secret": "whsec_...",
      "cloudflare_zone_id": "...", "cloudflare_token": "...",
      "signup_url": "https://..."}]

The key and secret are chosen by the operator here (instead of printed by
the CLI), stored the same way: the key as its hash, the secret encrypted.
Remove the variable once the deploy has applied it -- the tenant lives in
the database, and the variable would otherwise keep the key in plaintext.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.crypto import encrypt
from app.models import Tenant
from app.security import KEY_PREFIX, hash_key

logger = logging.getLogger(__name__)


def apply(db: Session, settings: Settings, raw: str | None) -> list[str]:
    if not raw:
        return []
    entries: list[dict[str, Any]] = json.loads(raw)
    applied = []
    for entry in entries:
        slug = str(entry["slug"])
        key = entry.get("api_key")
        if key is not None and (not str(key).startswith(KEY_PREFIX) or len(str(key)) < 40):
            raise ValueError(f"bootstrap tenant {slug}: api_key must be a sc_live_ key")
        tenant = db.scalars(select(Tenant).where(Tenant.slug == slug)).first()
        if tenant is None:
            if not key or not entry.get("webhook_secret"):
                raise ValueError(f"bootstrap tenant {slug}: a new tenant needs a key and secret")
            tenant = Tenant(slug=slug, name=str(entry.get("name") or slug),
                            api_key_prefix="", api_key_hash="", webhook_secret_enc="")
            db.add(tenant)
        if key:
            tenant.api_key_prefix = str(key)[:14]
            tenant.api_key_hash = hash_key(str(key))
        if entry.get("webhook_secret"):
            tenant.webhook_secret_enc = encrypt(settings, str(entry["webhook_secret"]))
        if "name" in entry:
            tenant.name = str(entry["name"])
        if "origins" in entry:
            tenant.return_origins = " ".join(str(o).rstrip("/") for o in entry["origins"])
        for field in ("webhook_url", "subdomain_base", "signup_url", "cloudflare_zone_id"):
            if field in entry:
                setattr(tenant, field, entry[field] or None)
        if "cloudflare_token" in entry:
            token = entry["cloudflare_token"]
            tenant.cloudflare_token_enc = encrypt(settings, str(token)) if token else None
        applied.append(slug)
    db.commit()
    logger.info("Bootstrap tenants applied", extra={"tenants": applied})
    return applied
