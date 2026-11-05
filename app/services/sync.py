"""What a tenant's product becomes in its shop's Shopify store.

One product, one default variant, created with ``productSet`` the first time
and updated in place after that. Two things are never guessed: a price in a
currency the store does not sell in, and a missing price. Both are recorded
as SKIPPED with the reason, and the tenant is told.
"""

from __future__ import annotations

import hashlib
import html
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
from app.models import Job, Product, Store, SyncState
from app.services import events
from app.shopify import ShopifyClient, ShopifyError, raise_user_errors

logger = logging.getLogger(__name__)

UPSERT = "product.upsert"
DELETE = "product.delete"
MAX_ATTEMPTS = 8
BATCH = 20

SHOP_QUERY = "{ shop { currencyCode primaryDomain { host } } }"
PRODUCT_SET = """
mutation productSet($input: ProductSetInput!) {
  productSet(synchronous: true, input: $input) {
    product { id handle status variants(first: 1) { nodes { id } } }
    userErrors { field message }
  }
}
"""
PRODUCT_DELETE = """
mutation productDelete($input: ProductDeleteInput!) {
  productDelete(input: $input) { deletedProductId userErrors { field message } }
}
"""
PUBLICATIONS = "{ publications(first: 20) { nodes { id name } } }"
PUBLISH = """
mutation publish($id: ID!, $input: [PublicationInput!]!) {
  publishablePublish(id: $id, input: $input) { userErrors { field message } }
}
"""
WEBHOOK_SUBSCRIBE = """
mutation subscribe($topic: WebhookSubscriptionTopic!, $url: URL!) {
  webhookSubscriptionCreate(
    topic: $topic, webhookSubscription: {callbackUrl: $url, format: JSON}
  ) { userErrors { field message } }
}
"""
WEBHOOK_TOPICS = ("APP_UNINSTALLED", "SHOP_UPDATE")


class SkipProduct(Exception):
    pass


def client_for(store: Store, settings: Settings, http: httpx.Client) -> ShopifyClient:
    assert store.shop_domain and store.access_token_enc
    token = decrypt(settings, store.access_token_enc)
    return ShopifyClient(http, store.shop_domain, token, settings.shopify_api_version)


def refresh_shop(store: Store, client: ShopifyClient) -> None:
    shop = client.graphql(SHOP_QUERY).get("shop") or {}
    store.currency = shop.get("currencyCode")
    host = (shop.get("primaryDomain") or {}).get("host")
    if host:
        store.primary_domain = host


def register_webhooks(client: ShopifyClient, callback_url: str) -> None:
    for topic in WEBHOOK_TOPICS:
        result = (
            client.graphql(WEBHOOK_SUBSCRIBE, {"topic": topic, "url": callback_url}).get(
                "webhookSubscriptionCreate"
            )
            or {}
        )
        raise_user_errors(result, ignore=("taken", "already"))


def _description_html(text: str | None) -> str:
    if not text:
        return ""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "".join("<p>" + html.escape(p).replace("\n", "<br>") + "</p>" for p in paragraphs)


def product_input(product: Product) -> dict[str, Any]:
    d = product.desired
    store = product.store
    if d.get("price") in (None, ""):
        raise SkipProduct("no price")
    if store.currency and d.get("currency") and d["currency"] != store.currency:
        raise SkipProduct(f"priced in {d['currency']}, the store sells in {store.currency}")
    data: dict[str, Any] = {
        "title": d["title"],
        "descriptionHtml": _description_html(d.get("description")),
        "status": "ACTIVE" if d.get("available", True) else "DRAFT",
        "productOptions": [{"name": "Title", "values": [{"name": "Default Title"}]}],
        "variants": [
            {
                "optionValues": [{"optionName": "Title", "name": "Default Title"}],
                "price": f"{float(d['price']):.2f}",
            }
        ],
    }
    if d.get("vendor"):
        data["vendor"] = d["vendor"]
    images = [u for u in d.get("images") or [] if isinstance(u, str) and u.startswith("https://")]
    if images:
        data["files"] = [
            {"originalSource": u, "contentType": "IMAGE"} for u in dict.fromkeys(images)
        ]
    return data


def content_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _online_store_publication(client: ShopifyClient) -> str | None:
    nodes = (client.graphql(PUBLICATIONS).get("publications") or {}).get("nodes") or []
    return next((str(n["id"]) for n in nodes if n.get("name") == "Online Store"), None)


def push(db: Session, product: Product, client: ShopifyClient) -> None:
    store = product.store
    try:
        data = product_input(product)
    except SkipProduct as skip:
        product.state = SyncState.SKIPPED
        product.last_error = str(skip)
        logger.info("Product skipped", extra={"product_id": str(product.id), "reason": str(skip)})
        events.emit(db, store.tenant_id, "product.failed", events.product_payload(product))
        return
    digest = content_hash(data)
    if product.shopify_product_id and product.synced_hash == digest:
        # Nothing Shopify sees changed: no call, and the state is what it was.
        product.state = SyncState.SYNCED
        product.last_error = None
        return
    created = product.shopify_product_id is None
    if not created:
        data["id"] = product.shopify_product_id
    result = client.graphql(PRODUCT_SET, {"input": data}).get("productSet") or {}
    raise_user_errors(result)
    shopify_product = result.get("product") or {}
    variants = (shopify_product.get("variants") or {}).get("nodes") or []
    if created:
        publication = _online_store_publication(client)
        if publication:
            published = client.graphql(
                PUBLISH, {"id": shopify_product["id"], "input": [{"publicationId": publication}]}
            ).get("publishablePublish") or {}
            raise_user_errors(published)
    now = datetime.now(UTC)
    product.shopify_product_id = str(shopify_product["id"])
    product.shopify_variant_id = str(variants[0]["id"]) if variants else None
    product.handle = str(shopify_product.get("handle") or "")
    product.active = shopify_product.get("status") == "ACTIVE"
    product.state = SyncState.SYNCED
    product.synced_hash = digest
    product.synced_at = now
    product.last_error = None
    store.last_sync_at = now
    logger.info(
        "Product pushed",
        extra={"product_id": str(product.id), "action": "created" if created else "updated"},
    )
    events.emit(db, store.tenant_id, "product.synced", events.product_payload(product))


def enqueue(db: Session, store: Store, kind: str, payload: dict[str, Any]) -> None:
    db.add(
        Job(kind=kind, store_id=store.id, payload=payload, attempts=0,
            next_attempt_at=datetime.now(UTC))
    )


def _handle(db: Session, job: Job, settings: Settings, http: httpx.Client) -> None:
    store = db.get(Store, job.store_id)
    if store is None or not store.is_live:
        return
    client = client_for(store, settings, http)
    if job.kind == UPSERT:
        product = db.get(Product, uuid.UUID(job.payload["product_id"]))
        if product is not None and product.store_id == store.id:
            push(db, product, client)
    elif job.kind == DELETE:
        result = client.graphql(
            PRODUCT_DELETE, {"input": {"id": job.payload["shopify_product_id"]}}
        ).get("productDelete") or {}
        raise_user_errors(result, ignore=("not exist",))
        logger.info("Product deleted", extra={"store_id": str(store.id)})


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(30 * 2 ** max(attempts - 1, 0), 3600))


def drain_once(
    session_factory: sessionmaker[Session], settings: Settings, http: httpx.Client
) -> int:
    finished = 0
    now = datetime.now(UTC)
    with session_factory() as db:
        ids = list(
            db.scalars(
                select(Job.id)
                .where(Job.done_at.is_(None), Job.next_attempt_at <= now)
                .order_by(Job.created_at)
                .limit(BATCH)
            )
        )
    for job_id in ids:
        with session_factory() as db:
            job = db.scalars(
                select(Job).where(Job.id == job_id, Job.done_at.is_(None))
                .with_for_update(skip_locked=True)
            ).first()
            if job is None:
                continue
            try:
                _handle(db, job, settings, http)
            except Exception as exc:
                db.rollback()
                job = db.get(Job, job_id)
                if job is None:
                    continue
                message = str(exc)[:2000] if isinstance(exc, ShopifyError) else type(exc).__name__
                job.attempts += 1
                job.last_error = message
                store = db.get(Store, job.store_id)
                if store is not None:
                    store.last_error = message
                if job.attempts >= MAX_ATTEMPTS:
                    job.done_at = datetime.now(UTC)
                    _fail_product(db, job, message)
                else:
                    job.next_attempt_at = datetime.now(UTC) + _backoff(job.attempts)
                db.commit()
                continue
            job.done_at = datetime.now(UTC)
            job.last_error = None
            db.commit()
            finished += 1
    return finished


def _fail_product(db: Session, job: Job, message: str) -> None:
    if job.kind != UPSERT:
        return
    product = db.get(Product, uuid.UUID(job.payload["product_id"]))
    if product is None:
        return
    product.state = SyncState.FAILED
    product.last_error = message
    events.emit(db, product.store.tenant_id, "product.failed", events.product_payload(product))
