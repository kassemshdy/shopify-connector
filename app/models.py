"""The connector's entities.

- ``Tenant``: a website using the connector (Janoubona, and the next one).
  It authenticates with an API key and receives signed webhooks.
- ``Store``: one Shopify store, connected on behalf of one of the tenant's
  shops. ``external_ref`` is the tenant's own id for that shop; the
  connector never needs to know anything else about it.
- ``Product``: one of the tenant's products, as last sent, and what it became
  in Shopify. ``external_ref`` is the tenant's product id.
- ``Job``: a push to Shopify waiting to be sent.
- ``WebhookDelivery``: an event waiting to be delivered to a tenant.

Every query a tenant can trigger is scoped by ``tenant_id``.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, Timestamps, uuid_pk


class StoreStatus(str, enum.Enum):
    PENDING = "PENDING"  # connect started, owner not back from Shopify yet
    CONNECTED = "CONNECTED"
    UNINSTALLED = "UNINSTALLED"


class SyncState(str, enum.Enum):
    QUEUED = "QUEUED"
    SYNCED = "SYNCED"
    SKIPPED = "SKIPPED"  # deliberately not pushed (currency, no price)
    FAILED = "FAILED"
    DELETED = "DELETED"


def _enum(e: type[enum.Enum], name: str) -> SAEnum:
    return SAEnum(e, name=name, values_callable=lambda x: [m.value for m in x])


class Tenant(Base, Timestamps):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    api_key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    webhook_secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    # Space-separated origins a connect may return to, e.g. "https://janoubona.net".
    return_origins: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # "janoubona.net": stores get "<sub>.janoubona.net". Null: no subdomains.
    subdomain_base: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cloudflare_zone_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cloudflare_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    signup_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    @property
    def origins(self) -> list[str]:
        return [o.rstrip("/") for o in self.return_origins.split() if o]


class Store(Base, Timestamps):
    __tablename__ = "stores"
    __table_args__ = (UniqueConstraint("tenant_id", "external_ref", name="uq_stores_tenant_ref"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    # One Shopify store belongs to one tenant's shop at a time.
    shop_domain: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    scopes: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    status: Mapped[StoreStatus] = mapped_column(
        _enum(StoreStatus, "store_status"), nullable=False, default=StoreStatus.PENDING
    )
    primary_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    subdomain: Mapped[str | None] = mapped_column(String(63), nullable=True)
    cf_record_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    tenant: Mapped[Tenant] = relationship()

    @property
    def is_live(self) -> bool:
        return self.status is StoreStatus.CONNECTED and self.access_token_enc is not None

    @property
    def storefront_url(self) -> str | None:
        host = self.primary_domain or self.shop_domain
        return f"https://{host}" if self.is_live and host else None


class Product(Base, Timestamps):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("store_id", "external_ref", name="uq_products_store_ref"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    desired: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    shopify_product_id: Mapped[str | None] = mapped_column(String(80))
    shopify_variant_id: Mapped[str | None] = mapped_column(String(80))
    handle: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    state: Mapped[SyncState] = mapped_column(
        _enum(SyncState, "sync_state"), nullable=False, default=SyncState.QUEUED
    )
    synced_hash: Mapped[str | None] = mapped_column(String(64))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    store: Mapped[Store] = relationship()

    @property
    def buy_url(self) -> str | None:
        base = self.store.storefront_url
        if not base or not self.active or not self.handle or self.state is not SyncState.SYNCED:
            return None
        return f"{base}/products/{self.handle}"


class Job(Base, Timestamps):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_pending", "done_at", "next_attempt_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class WebhookDelivery(Base, Timestamps):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_webhook_deliveries_pending", "delivered_at", "next_attempt_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gave_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
