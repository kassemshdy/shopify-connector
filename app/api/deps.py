from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

import httpx
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.errors import NotFound, Unauthorized
from app.models import Tenant
from app.services import tenants

DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def get_http() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=get_settings().http_timeout_seconds) as client:
        yield client


Http = Annotated[httpx.Client, Depends(get_http)]


def require_shopify(settings: AppSettings) -> Settings:
    if not settings.shopify_enabled:
        raise NotFound("not_configured", "The Shopify app is not configured.")
    return settings


ShopifySettings = Annotated[Settings, Depends(require_shopify)]


def current_tenant(request: Request, db: DbSession) -> Tenant:
    scheme, _, key = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not key.strip():
        raise Unauthorized("bad_api_key", "Missing or invalid API key.")
    return tenants.authenticate(db, key.strip())


CurrentTenant = Annotated[Tenant, Depends(current_tenant)]
