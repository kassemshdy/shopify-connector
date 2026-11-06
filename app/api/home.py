"""The app's page in a merchant's Shopify admin (the app's "App URL").

Shopify opens it with ``shop``, ``timestamp`` and an ``hmac`` signed with the
app's secret, inside the admin when the app is embedded. The page shows only
what the store's owner can already see on their own website -- which site the
store is connected to and how the product sync is going -- and only after the
signature and its freshness check out. Without them it is a plain description
of the app, with nothing about any store.
"""

from __future__ import annotations

import time
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from app.api.deps import AppSettings, DbSession
from app.models import Product, Store, StoreStatus, SyncState
from app.shopify import is_shop_domain, verify_query_hmac

router = APIRouter(include_in_schema=False)

# Shopify signs the launch with the current time; an older link is refused so a
# copied URL does not keep showing a store's status.
LAUNCH_MAX_AGE_SECONDS = 600

APP_BRIDGE = "https://cdn.shopify.com/shopifycloud/app-bridge.js"


def _page(body: str, name: str, *, api_key: str | None = None) -> str:
    bridge = (
        f'<meta name="shopify-api-key" content="{escape(api_key)}">'
        f'<script src="{APP_BRIDGE}"></script>'
        if api_key else ""
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(name)}</title>{bridge}
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  margin: 0; padding: 24px; background: #f6f6f7; color: #202223; }}
main {{ max-width: 640px; margin: 0 auto; background: #fff; border-radius: 12px;
  padding: 24px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
h1 {{ font-size: 20px; margin: 0 0 12px; }}
p {{ line-height: 1.5; }}
dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 8px 16px; }}
dt {{ color: #6d7175; }} dd {{ margin: 0; }}
a.button {{ display: inline-block; margin-top: 16px; padding: 10px 16px; border-radius: 8px;
  background: #008060; color: #fff; text-decoration: none; }}
.error {{ color: #8e1f0b; }}
h2 {{ font-size: 16px; margin: 20px 0 8px; }}
</style></head><body><main>{body}</main></body></html>"""


def _response(html: str, frame_ancestors: str) -> HTMLResponse:
    return HTMLResponse(
        html,
        headers={
            "Content-Security-Policy": f"frame-ancestors {frame_ancestors};",
            "Cache-Control": "no-store",
        },
    )


def _generic(name: str) -> str:
    return (
        f"<h1>{escape(name)}</h1>"
        "<p>This app keeps a shop's products on a directory website in step with its "
        "Shopify store. A store is connected from the website where the shop is "
        "listed, not from here.</p>"
    )


@router.get("/")
def home(request: Request, db: DbSession, settings: AppSettings) -> HTMLResponse:
    params = dict(request.query_params)
    shop = params.get("shop", "")
    secret = settings.shopify_api_secret
    try:
        age = abs(time.time() - int(params.get("timestamp", "0")))
    except ValueError:
        age = float("inf")
    launched = bool(
        secret
        and is_shop_domain(shop)
        and verify_query_hmac(params, secret)
        and age <= LAUNCH_MAX_AGE_SECONDS
    )
    if not launched:
        return _response(_page(_generic(settings.app_name), settings.app_name), "'none'")

    frame = f"https://{shop} https://admin.shopify.com"
    store = db.scalars(select(Store).where(Store.shop_domain == shop)).first()
    if store is None or store.status is not StoreStatus.CONNECTED:
        body = (
            "<h1>Not connected yet</h1>"
            f"<p>{escape(shop)} is not connected to a website. Open your shop's "
            "dashboard on the website where it is listed and connect this store "
            "from there; your products will then appear here.</p>"
        )
        return _response(_page(body, settings.app_name, api_key=settings.shopify_api_key), frame)

    counts: dict[SyncState, int] = {
        state: count
        for state, count in db.execute(
            select(Product.state, func.count())
            .where(Product.store_id == store.id)
            .group_by(Product.state)
        ).tuples()
    }
    tenant = store.tenant
    rows = [
        ("Website", escape(tenant.name)),
        ("Products in your store", str(counts.get(SyncState.SYNCED, 0))),
        ("Waiting to be sent", str(counts.get(SyncState.QUEUED, 0))),
        ("Not sent", str(counts.get(SyncState.FAILED, 0) + counts.get(SyncState.SKIPPED, 0))),
        ("Last sent", escape(store.last_sync_at.strftime("%Y-%m-%d %H:%M UTC"))
         if store.last_sync_at else "Not yet"),
    ]
    body = (
        f"<h1>Connected to {escape(tenant.name)}</h1>"
        "<p>Products you add or change on the website are sent to this store "
        "automatically. Edit them on the website; a change made here may be "
        "replaced by the next one sent from there.</p>"
        "<dl>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows) + "</dl>"
    )
    if store.last_error:
        body += f'<p class="error">Last problem: {escape(store.last_error)}</p>'
    if tenant.origins:
        body += (
            f'<a class="button" href="{escape(tenant.origins[0])}" target="_top" '
            f'rel="noopener">Open {escape(tenant.name)}</a>'
        )
    return _response(_page(body, settings.app_name, api_key=settings.shopify_api_key), frame)


# PLACEHOLDER COPY: the two pages below are written to pass Shopify's review and
# say only what the code does; the owners have not yet chosen their wording.
_UPDATED = "6 November 2026"


def _contact(settings: AppSettings) -> str:
    email = settings.support_email
    if not email:
        return "<p>Contact the website where your shop is listed.</p>"
    return f'<p>Email <a href="mailto:{escape(email)}">{escape(email)}</a>.</p>'


@router.get("/privacy")
def privacy(settings: AppSettings) -> HTMLResponse:
    name = escape(settings.app_name)
    body = f"""<h1>{name} privacy policy</h1>
<p>Last updated {_UPDATED}.</p>
<p>{name} connects a merchant's Shopify store to the directory website where the shop
is listed, and sends the products the merchant manages on that website to the store.</p>
<h2>What we store</h2>
<ul>
<li>The store's address (<code>*.myshopify.com</code>), name, currency and the access
token Shopify issues at installation. The token is encrypted at rest.</li>
<li>The products sent from the website and the Shopify ids they were given.</li>
<li>When the website asks for it, the shop's logo and social links, written into the
store's theme.</li>
</ul>
<h2>Orders and payments</h2>
<p>The app is shared with Yalla Pay, our payment partner, which uses its order and
inventory access to take payments and create the resulting orders in the store. That
data is handled by Yalla Pay under its own policy. This service itself stores no
customer, order or payment data.</p>
<h2>Who processes it</h2>
<p>The app runs on Railway; DNS for store addresses is managed on Cloudflare; error
reports go to Sentry with personal data removed.</p>
<h2>Deletion</h2>
<p>Uninstalling the app deletes the access token at once. 48 hours later Shopify
asks us to erase the store, and every record of it is deleted.</p>
<h2>Contact</h2>{_contact(settings)}"""
    return _response(_page(body, settings.app_name), "'none'")


@router.get("/support")
def support(settings: AppSettings) -> HTMLResponse:
    name = escape(settings.app_name)
    body = f"""<h1>{name} support</h1>
<p>Your store is connected, and your products are managed, from the website where your
shop is listed. Open the app in your Shopify admin to see whether the store is connected
and how the product sync is going.</p>
<h2>Contact</h2>{_contact(settings)}
<p><a href="/privacy">Privacy policy</a></p>"""
    return _response(_page(body, settings.app_name), "'none'")
