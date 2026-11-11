from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class ConnectIn(BaseModel):
    shop: str = Field(min_length=1, max_length=255)
    return_url: HttpUrl
    # The shop's own page on the website; must be on the tenant's origins.
    page_url: HttpUrl | None = None


class PageIn(BaseModel):
    page_url: HttpUrl


class RateIn(BaseModel):
    name: str = Field(default="Standard Delivery", min_length=1, max_length=60)
    price: Decimal = Field(ge=0, le=10000, decimal_places=2)
    description: str | None = Field(default=None, max_length=120)


class SetupIn(BaseModel):
    """Put a country in an active market and give it a delivery rate."""

    country: str = Field(default="LB", pattern="^[A-Z]{2}$")
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    rate: RateIn


class ConnectOut(BaseModel):
    authorize_url: str


class SubdomainIn(BaseModel):
    subdomain: str = Field(min_length=2, max_length=63)


class TenantOut(BaseModel):
    slug: str
    name: str
    signup_url: str | None
    subdomain_base: str | None
    subdomains_available: bool


class StoreOut(BaseModel):
    store_ref: str
    status: str
    shop_domain: str | None
    storefront_url: str | None
    subdomain: str | None
    subdomain_hostname: str | None
    subdomain_live: bool
    currency: str | None
    connected_at: datetime | None
    last_sync_at: datetime | None
    last_error: str | None
    products_synced: int
    jobs_pending: int
    setup: dict[str, Any] | None = None
    signup_url: str | None


class ProductIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=20000)
    price: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    images: list[HttpUrl] = Field(default_factory=list, max_length=20)
    available: bool = True
    vendor: str | None = Field(default=None, max_length=255)

    def desired(self) -> dict[str, object]:
        return {
            "title": self.title,
            "description": self.description,
            "price": str(self.price) if self.price is not None else None,
            "currency": self.currency.upper() if self.currency else None,
            "images": [str(u) for u in self.images],
            "available": self.available,
            "vendor": self.vendor,
        }


class BulkItem(ProductIn):
    product_ref: str = Field(min_length=1, max_length=120)


class BulkIn(BaseModel):
    products: list[BulkItem] = Field(max_length=500)


class ProductOut(BaseModel):
    store_ref: str
    product_ref: str
    state: str
    handle: str | None
    active: bool
    buy_url: str | None
    error: str | None
    synced_at: datetime | None


class ProfileIn(BaseModel):
    """What a website knows about the shop, for its store's theme."""

    name: str | None = Field(default=None, max_length=255)
    # A public https address Shopify can fetch the logo from.
    logo_url: HttpUrl | None = None
    # e.g. {"instagram": "https://instagram.com/...", "facebook": "..."}
    social: dict[str, HttpUrl] = Field(default_factory=dict, max_length=12)


class Queued(BaseModel):
    queued: int
