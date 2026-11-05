"""A tenant telling us what its products are. Every write queues a job."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import Conflict, NotFound
from app.models import Product, Store, SyncState
from app.services import sync


def get_product(db: Session, store: Store, ref: str) -> Product:
    product = db.scalars(
        select(Product).where(Product.store_id == store.id, Product.external_ref == ref)
    ).first()
    if product is None:
        raise NotFound("product_not_found", "No product with this ref in this store.")
    return product


def upsert(db: Session, store: Store, ref: str, desired: dict[str, Any]) -> Product:
    if not store.is_live:
        raise Conflict("not_connected", "The store is not connected.")
    product = db.scalars(
        select(Product).where(Product.store_id == store.id, Product.external_ref == ref)
    ).first()
    if product is None:
        product = Product(store_id=store.id, external_ref=ref, desired=desired)
        product.store = store
        db.add(product)
    else:
        product.desired = desired
    product.state = SyncState.QUEUED
    db.flush()
    sync.enqueue(db, store, sync.UPSERT, {"product_id": str(product.id)})
    return product


def delete(db: Session, store: Store, ref: str) -> None:
    product = get_product(db, store, ref)
    if product.shopify_product_id and store.is_live:
        sync.enqueue(db, store, sync.DELETE, {"shopify_product_id": product.shopify_product_id})
    db.delete(product)
