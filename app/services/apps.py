"""Which Shopify app serves a store: its own (custom distribution) or the default.

Each store may have an app made only for it, registered on the operator page
with its client id and secret (the secret encrypted like store tokens). A
store without one uses the default app from the settings. Everything that
talks to Shopify on a store's behalf -- the grant screen, the code exchange,
the token exchange at install, and checking what Shopify sends us -- asks
here for that store's credentials.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import shopify
from app.config import Settings
from app.crypto import decrypt, encrypt
from app.errors import Conflict, Invalid
from app.models import ShopifyApp


@dataclass(frozen=True)
class Credentials:
    client_id: str
    secret: str


def for_shop(db: Session, settings: Settings, shop: str) -> Credentials | None:
    """The app for this store, or the default app; None if neither is configured."""
    app = db.scalars(select(ShopifyApp).where(ShopifyApp.shop_domain == shop)).first()
    if app is not None:
        return Credentials(app.client_id, decrypt(settings, app.secret_enc))
    if settings.shopify_api_key and settings.shopify_api_secret:
        return Credentials(settings.shopify_api_key, settings.shopify_api_secret)
    return None


def secrets_for(db: Session, settings: Settings, shop: str) -> list[str]:
    """Every secret Shopify may sign this store's requests with: its own app first."""
    own = db.scalars(select(ShopifyApp).where(ShopifyApp.shop_domain == shop)).first()
    first = [decrypt(settings, own.secret_enc)] if own is not None else []
    return first + [s for s in settings.shopify_secrets() if s not in first]


def add(db: Session, settings: Settings, *, shop: str, client_id: str, secret: str,
        name: str) -> ShopifyApp:
    shop = shopify.normalize_shop(shop)
    client_id, secret = client_id.strip(), secret.strip()
    if not shopify.is_shop_domain(shop):
        raise Invalid("bad_shop", "Expected a <name>.myshopify.com store address.")
    if not client_id or not secret:
        raise Invalid("bad_app", "Both the client id and the secret are needed.")
    if client_id == settings.shopify_api_key:
        raise Invalid("default_app", "This is the default app's client id. Use the client "
                                     "id of the store's own app (Dev Dashboard → the app → "
                                     "Settings).")
    taken = db.scalars(select(ShopifyApp).where(
        (ShopifyApp.shop_domain == shop) | (ShopifyApp.client_id == client_id)
    )).first()
    if taken is not None:
        raise Conflict("app_exists", "This store or this client id already has an app.")
    app = ShopifyApp(shop_domain=shop, client_id=client_id,
                     secret_enc=encrypt(settings, secret), name=name.strip()[:120])
    db.add(app)
    db.commit()
    return app
