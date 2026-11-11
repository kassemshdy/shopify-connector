"""Events for tenants: queued with the change that caused them, delivered
by the worker, signed with the tenant's webhook secret.

Payloads carry the tenant's own refs, so a tenant never has to map our ids.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.crypto import decrypt
from app.models import Product, Store, Tenant, WebhookDelivery
from app.security import sign

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 10
BATCH = 50


def store_payload(store: Store) -> dict[str, Any]:
    base = store.tenant.subdomain_base if store.tenant else None
    return {
        "store_ref": store.external_ref,
        "status": store.status.value,
        "shop_domain": store.shop_domain,
        "storefront_url": store.storefront_url,
        "subdomain": store.subdomain,
        "subdomain_hostname": f"{store.subdomain}.{base}" if store.subdomain and base else None,
        "currency": store.currency,
        "last_error": store.last_error,
        "setup": store.setup_report,
    }


def product_payload(product: Product) -> dict[str, Any]:
    return {
        "store_ref": product.store.external_ref,
        "product_ref": product.external_ref,
        "state": product.state.value,
        "handle": product.handle,
        "active": product.active,
        "buy_url": product.buy_url,
        "error": product.last_error,
    }


def emit(db: Session, tenant_id: uuid.UUID, event: str, data: dict[str, Any]) -> None:
    db.add(
        WebhookDelivery(
            tenant_id=tenant_id,
            event=event,
            payload={
                "id": str(uuid.uuid4()),
                "event": event,
                "created_at": datetime.now(UTC).isoformat(),
                "data": data,
            },
            attempts=0,
            next_attempt_at=datetime.now(UTC),
        )
    )


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(30 * 2 ** max(attempts - 1, 0), 6 * 3600))


def deliver_once(
    session_factory: sessionmaker[Session], settings: Settings, http: httpx.Client
) -> int:
    now = datetime.now(UTC)
    delivered = 0
    with session_factory() as db:
        ids = list(
            db.scalars(
                select(WebhookDelivery.id)
                .where(
                    WebhookDelivery.delivered_at.is_(None),
                    WebhookDelivery.gave_up_at.is_(None),
                    WebhookDelivery.next_attempt_at <= now,
                )
                .order_by(WebhookDelivery.created_at)
                .limit(BATCH)
            )
        )
    for delivery_id in ids:
        with session_factory() as db:
            delivery = db.scalars(
                select(WebhookDelivery)
                .where(WebhookDelivery.id == delivery_id, WebhookDelivery.delivered_at.is_(None))
                .with_for_update(skip_locked=True)
            ).first()
            if delivery is None:
                continue
            tenant = db.get(Tenant, delivery.tenant_id)
            if tenant is None or not tenant.webhook_url or not tenant.active:
                # Nowhere to send it; keep it for when a URL is set.
                delivery.next_attempt_at = now + timedelta(hours=1)
                db.commit()
                continue
            body = json.dumps(delivery.payload, separators=(",", ":")).encode()
            secret = decrypt(settings, tenant.webhook_secret_enc)
            try:
                response = http.post(
                    tenant.webhook_url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Connector-Event": delivery.event,
                        "X-Connector-Delivery": str(delivery.id),
                        "X-Connector-Signature": sign(secret, body),
                    },
                )
                ok = 200 <= response.status_code < 300
                error = None if ok else f"tenant answered {response.status_code}"
            except httpx.HTTPError as exc:
                ok, error = False, f"{type(exc).__name__}"
            delivery.attempts += 1
            if ok:
                delivery.delivered_at = datetime.now(UTC)
                delivery.last_error = None
                delivered += 1
            else:
                delivery.last_error = error
                if delivery.attempts >= MAX_ATTEMPTS:
                    delivery.gave_up_at = datetime.now(UTC)
                    logger.error("Webhook given up", extra={"tenant": tenant.slug})
                else:
                    delivery.next_attempt_at = datetime.now(UTC) + _backoff(delivery.attempts)
            db.commit()
    return delivered
