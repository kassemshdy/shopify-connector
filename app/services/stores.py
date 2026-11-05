"""Connecting a tenant's shop to its Shopify store, and everything after.

1. ``start_connect``: the tenant asks for an authorize URL for its shop
   ``store_ref``. The shop name is checked, the ``return_url`` must be on the
   tenant's allow-list (otherwise this would be an open redirect), and a
   signed ``state`` carries tenant, store ref, shop and return URL, because
   Shopify's redirect arrives without the tenant's key.
2. ``complete_connect``: Shopify's HMAC, the state and the shop are all
   checked; the offline token is stored encrypted; the store is subscribed to
   the webhooks we handle; the tenant is told ``store.connected``. The return
   URL gets ``?status=connected`` or an error code.
3. ``disconnect`` / ``forget``: the token is revoked and the DNS record
   removed. Products already in Shopify stay: they are the shop owner's.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import shopify
from app.cloudflare import RESERVED, SUBDOMAIN, CloudflareDns, CloudflareError
from app.config import Settings
from app.crypto import decrypt, encrypt
from app.errors import Conflict, Invalid, NotFound
from app.models import Product, Store, StoreStatus, Tenant
from app.services import events
from app.services.sync import client_for, refresh_shop, register_webhooks

logger = logging.getLogger(__name__)

STATE_AUDIENCE = "connector-install"
STATE_TTL = timedelta(minutes=15)
CALLBACK_PATH = "/shopify/callback"
WEBHOOK_PATH = "/shopify/webhooks"


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".rstrip("/")


def with_status(return_url: str, status: str) -> str:
    joiner = "&" if "?" in return_url else "?"
    return f"{return_url}{joiner}{urlencode({'shopify': status})}"


def get_store(db: Session, tenant: Tenant, ref: str) -> Store:
    store = db.scalars(
        select(Store).where(Store.tenant_id == tenant.id, Store.external_ref == ref)
    ).first()
    if store is None:
        raise NotFound("store_not_found", "No store with this ref for this tenant.")
    return store


class StoreService:
    def __init__(self, db: Session, settings: Settings, http: httpx.Client) -> None:
        self.db = db
        self.settings = settings
        self.http = http

    # --- connect ------------------------------------------------------------

    def start_connect(self, tenant: Tenant, ref: str, shop_input: str, return_url: str) -> str:
        shop = shopify.normalize_shop(shop_input)
        if not shopify.is_shop_domain(shop):
            raise Invalid("bad_shop", "Expected a <name>.myshopify.com store address.")
        if _origin(return_url) not in tenant.origins:
            raise Invalid("bad_return_url", "return_url is not on this tenant's allow-list.")
        now = datetime.now(UTC)
        state = jwt.encode(
            {
                "aud": STATE_AUDIENCE,
                "tid": str(tenant.id),
                "ref": ref,
                "shop": shop,
                "ret": return_url,
                "nonce": secrets.token_urlsafe(12),
                "iat": int(now.timestamp()),
                "exp": int((now + STATE_TTL).timestamp()),
            },
            self.settings.secret_key,
            algorithm="HS256",
        )
        store = self.db.scalars(
            select(Store).where(Store.tenant_id == tenant.id, Store.external_ref == ref)
        ).first()
        if store is None:
            self.db.add(Store(tenant_id=tenant.id, external_ref=ref, status=StoreStatus.PENDING))
            self.db.commit()
        assert self.settings.shopify_api_key
        return shopify.authorize_url(
            shop,
            api_key=self.settings.shopify_api_key,
            scopes=self.settings.shopify_scopes,
            redirect_uri=self.settings.public_base_url.rstrip("/") + CALLBACK_PATH,
            state=state,
        )

    def read_state(self, state: str) -> dict[str, Any]:
        try:
            claims: dict[str, Any] = jwt.decode(
                state, self.settings.secret_key, algorithms=["HS256"], audience=STATE_AUDIENCE
            )
            return claims
        except jwt.PyJWTError as exc:
            raise Invalid("bad_state", "The connection could not be confirmed.") from exc

    def complete_connect(self, params: dict[str, str]) -> str:
        """Return the URL to send the owner's browser to."""
        assert self.settings.shopify_api_key and self.settings.shopify_api_secret
        if not shopify.verify_query_hmac(params, self.settings.shopify_api_secret):
            raise Invalid("bad_hmac", "Shopify's signature did not match.")
        claims = self.read_state(params.get("state", ""))
        return_url = str(claims["ret"])
        shop = params.get("shop", "")
        if not shopify.is_shop_domain(shop) or shop != claims.get("shop"):
            return with_status(return_url, "shop_mismatch")
        tenant = self.db.get(Tenant, uuid.UUID(claims["tid"]))
        if tenant is None or not tenant.active:
            return with_status(return_url, "tenant_inactive")
        store = self.db.scalars(
            select(Store).where(Store.tenant_id == tenant.id, Store.external_ref == claims["ref"])
        ).first()
        if store is None:
            return with_status(return_url, "store_not_found")
        taken = self.db.scalars(
            select(Store).where(Store.shop_domain == shop, Store.id != store.id)
        ).first()
        if taken is not None:
            return with_status(return_url, "shop_taken")

        token, scopes = shopify.exchange_code(
            self.http,
            shop,
            api_key=self.settings.shopify_api_key,
            api_secret=self.settings.shopify_api_secret,
            code=params.get("code", ""),
        )
        if store.shop_domain and store.shop_domain != shop:
            store.primary_domain = None
        store.shop_domain = shop
        store.access_token_enc = encrypt(self.settings, token)
        store.scopes = scopes
        store.status = StoreStatus.CONNECTED
        store.connected_at = datetime.now(UTC)
        store.last_error = None
        try:
            client = client_for(store, self.settings, self.http)
            refresh_shop(store, client)
            register_webhooks(client, self.settings.public_base_url.rstrip("/") + WEBHOOK_PATH)
        except shopify.ShopifyError as exc:
            store.last_error = str(exc)
        events.emit(self.db, tenant.id, "store.connected", events.store_payload(store))
        self.db.commit()
        logger.info("Store connected", extra={"store_id": str(store.id), "tenant": tenant.slug})
        return with_status(return_url, "connected")

    # --- after --------------------------------------------------------------

    def disconnect(self, tenant: Tenant, store: Store) -> None:
        if store.is_live:
            try:
                assert store.shop_domain and store.access_token_enc
                shopify.revoke_token(
                    self.http, store.shop_domain, decrypt(self.settings, store.access_token_enc)
                )
            except Exception:
                logger.warning("Revoking a store token failed", exc_info=True)
        self.release_subdomain(tenant, store)
        events.emit(
            self.db, tenant.id, "store.disconnected",
            {**events.store_payload(store), "status": "DISCONNECTED", "storefront_url": None},
        )
        self.db.delete(store)
        self.db.commit()

    def assign_subdomain(self, tenant: Tenant, store: Store, raw: str) -> Store:
        dns = self._dns(tenant)
        if dns is None or not tenant.subdomain_base:
            raise NotFound("subdomains_off", "Subdomains are not configured for this tenant.")
        if not store.is_live:
            raise Conflict("not_connected", "The store is not connected.")
        sub = raw.strip().lower()
        if not SUBDOMAIN.fullmatch(sub):
            raise Invalid("bad_subdomain", "Use 2-31 Latin letters, digits or single hyphens.")
        if sub in RESERVED:
            raise Invalid("reserved_subdomain", "This name is reserved.")
        if sub == store.subdomain:
            return store
        taken = self.db.scalars(
            select(Store.id).where(
                Store.tenant_id == tenant.id, Store.subdomain == sub, Store.id != store.id
            )
        ).first()
        if taken is not None:
            raise Conflict("subdomain_taken", "This name is already used.")
        try:
            record_id = dns.create_cname(f"{sub}.{tenant.subdomain_base}")
        except (CloudflareError, httpx.HTTPError) as exc:
            raise Conflict("subdomain_failed", "The DNS record could not be created.") from exc
        old = store.cf_record_id
        store.subdomain = sub
        store.cf_record_id = record_id
        events.emit(self.db, tenant.id, "store.updated", events.store_payload(store))
        self.db.commit()
        if old:
            self._delete_record(dns, old)
        return store

    def release_subdomain(self, tenant: Tenant, store: Store) -> None:
        dns = self._dns(tenant)
        if dns is not None and store.cf_record_id:
            self._delete_record(dns, store.cf_record_id)
        store.cf_record_id = None
        store.subdomain = None

    def handle_shopify_webhook(self, topic: str, shop: str) -> None:
        store = self.db.scalars(select(Store).where(Store.shop_domain == shop)).first()
        if store is None:
            return
        tenant = store.tenant
        if topic == "app/uninstalled":
            store.status = StoreStatus.UNINSTALLED
            store.access_token_enc = None
            events.emit(self.db, tenant.id, "store.updated", events.store_payload(store))
            for product in self.db.scalars(select(Product).where(Product.store_id == store.id)):
                events.emit(self.db, tenant.id, "product.synced", events.product_payload(product))
        elif topic == "shop/update" and store.is_live:
            try:
                refresh_shop(store, client_for(store, self.settings, self.http))
            except shopify.ShopifyError as exc:
                store.last_error = str(exc)
            events.emit(self.db, tenant.id, "store.updated", events.store_payload(store))
        elif topic == "shop/redact":
            self.release_subdomain(tenant, store)
            events.emit(
                self.db, tenant.id, "store.disconnected",
                {**events.store_payload(store), "status": "DISCONNECTED", "storefront_url": None},
            )
            self.db.delete(store)
        # customers/data_request and customers/redact: no customer data is held.
        self.db.commit()

    # --- helpers ------------------------------------------------------------

    def _dns(self, tenant: Tenant) -> CloudflareDns | None:
        if not (tenant.cloudflare_token_enc and tenant.cloudflare_zone_id):
            return None
        return CloudflareDns(
            self.http, decrypt(self.settings, tenant.cloudflare_token_enc),
            tenant.cloudflare_zone_id,
        )

    @staticmethod
    def _delete_record(dns: CloudflareDns, record_id: str) -> None:
        try:
            dns.delete(record_id)
        except (CloudflareError, httpx.HTTPError):
            logger.warning("Deleting a DNS record failed", exc_info=True)
