"""The app's page in a merchant's Shopify admin (the app's "App URL").

Shopify opens it with ``shop``, ``timestamp`` and an ``hmac`` signed with the
app's secret, inside the admin when the app is embedded. The page shows only
what the store's owner can already see on their own website -- which site the
store is connected to and how the product sync is going -- and only after the
signature and its freshness check out. Without them it is a plain description
of the app, with nothing about any store.
"""

from __future__ import annotations

import logging
import time
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from app import shopify
from app.api.deps import AppSettings, DbSession, Http
from app.models import Installation, Product, Store, StoreStatus, SyncState
from app.services.stores import StoreService
from app.shopify import is_shop_domain, verify_query_hmac

router = APIRouter(include_in_schema=False)
logger = logging.getLogger(__name__)

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
def home(request: Request, db: DbSession, settings: AppSettings, http: Http) -> HTMLResponse:
    params = dict(request.query_params)
    shop = params.get("shop", "")
    try:
        age = abs(time.time() - int(params.get("timestamp", "0")))
    except ValueError:
        age = float("inf")
    launched = bool(
        is_shop_domain(shop)
        and any(verify_query_hmac(params, s) for s in settings.shopify_secrets())
        and age <= LAUNCH_MAX_AGE_SECONDS
    )
    if not launched:
        return _response(_page(_generic(settings.app_name), settings.app_name), "'none'")

    frame = f"https://{shop} https://admin.shopify.com"
    store = db.scalars(select(Store).where(Store.shop_domain == shop)).first()
    if store is None or store.status is not StoreStatus.CONNECTED:
        # Installed from Shopify (managed install): keep the store's token now,
        # from the session token Shopify passes, so no grant screen is needed.
        session_token = params.get("id_token", "")
        if (
            session_token
            and settings.shopify_api_key
            and shopify.verify_session_token(
                session_token, settings.shopify_api_key, settings.shopify_secrets()
            ) == shop
        ):
            try:
                StoreService(db, settings, http).install_from_launch(shop, session_token)
                store = db.scalars(select(Store).where(Store.shop_domain == shop)).first()
            except shopify.ShopifyError:
                logger.warning("Token exchange failed", extra={"shop": shop})
    if store is None:
        installed = db.scalars(
            select(Installation).where(Installation.shop_domain == shop)
        ).first()
        if installed is not None and installed.access_token_enc:
            body = (
                "<h1>Installed</h1>"
                f"<p>{escape(settings.app_name)} is installed on {escape(shop)}. The website "
                "where your shop is listed will now link this store, and your products will "
                "appear here once it does. There is nothing else to do.</p>"
            )
            return _response(
                _page(body, settings.app_name, api_key=settings.shopify_api_key), frame
            )
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
    # The shop's own page when the website told us it, else the website itself.
    if store.page_url:
        body += (
            f'<a class="button" href="{escape(store.page_url)}" target="_top" '
            f'rel="noopener">Open your page on {escape(tenant.name)}</a>'
        )
    elif tenant.origins:
        body += (
            f'<a class="button" href="{escape(tenant.origins[0])}" target="_top" '
            f'rel="noopener">Open {escape(tenant.name)}</a>'
        )
    return _response(_page(body, settings.app_name, api_key=settings.shopify_api_key), frame)


# PLACEHOLDER COPY: the pages below are written to pass Shopify's review and
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
<h2>Contact</h2>{_contact(settings)}
<p><a href="/terms">Merchant terms and data processing</a></p>"""
    return _response(_page(body, settings.app_name), "'none'")


@router.get("/terms")
def terms(settings: AppSettings) -> HTMLResponse:
    """The agreement a merchant accepts by installing: terms and data processing."""
    name = escape(settings.app_name)
    body = f"""<h1>{name} merchant terms and data processing</h1>
<p>Last updated {_UPDATED}.</p>
<p>These terms apply to every merchant who installs {name} on a Shopify store.
Installing or keeping the app installed means accepting them. {name} is operated by
Halyoon ("we").</p>
<h2>1. The service</h2>
<p>{name} connects a merchant's Shopify store to the directory website where the
merchant's shop is listed: it sends the products managed on that website to the store,
can set the store's market, delivery rate, logo and social links when the website asks,
and lets our payment partner Yalla Pay take payments and create the resulting orders.</p>
<h2>2. Roles</h2>
<p>For the personal data of the merchant's customers, the merchant is the controller and
we act as a processor on the merchant's behalf. We process it only to provide the
service described above, on the merchant's instructions given through Shopify and the
directory website, and never sell it or use it for advertising.</p>
<h2>3. What is processed</h2>
<p>Customer name, email, phone number and shipping or billing address, and the orders
they belong to, only as far as needed to create orders, calculate shipping and deliver
them. The connector itself stores none of it; see the <a href="/privacy">privacy
policy</a> for what it does store.</p>
<h2>4. Sub-processors</h2>
<ul>
<li>Yalla Pay: payments and order creation, under its own terms and policy.</li>
<li>Railway: hosting of the service and its database.</li>
<li>Cloudflare: DNS and network protection.</li>
<li>Sentry: error reports, with personal data removed.</li>
</ul>
<p>We will publish a change to this list here before it takes effect.</p>
<h2>5. Security</h2>
<p>Data travels only over HTTPS, store access tokens are encrypted at rest, access to
production systems is limited to the people who operate them, and logs and error reports
exclude personal data.</p>
<h2>6. Retention and deletion</h2>
<p>Uninstalling the app deletes the store's access token at once. When Shopify asks us
to erase a store's or a customer's data, we do so and confirm. Yalla Pay keeps the order
records it needs under its own retention policy.</p>
<h2>7. Incidents</h2>
<p>If we learn of a breach affecting a merchant's data, we will tell the merchant
without undue delay, and within 72 hours, with what we know and what we are doing.</p>
<h2>8. The merchant's part</h2>
<p>The merchant tells its customers, in its own store's privacy policy, that their data
is used to process and deliver their orders, and has the right to do so.</p>
<h2>9. Changes and contact</h2>
<p>We may update these terms; the date above shows the latest version.</p>
{_contact(settings)}"""
    return _response(_page(body, settings.app_name), "'none'")


@router.get("/support")
def support(settings: AppSettings) -> HTMLResponse:
    name = escape(settings.app_name)
    body = f"""<h1>{name} support</h1>
<p>Your store is connected, and your products are managed, from the website where your
shop is listed. Open the app in your Shopify admin to see whether the store is connected
and how the product sync is going.</p>
<h2>Contact</h2>{_contact(settings)}
<p><a href="/privacy">Privacy policy</a> &middot;
<a href="/terms">Merchant terms and data processing</a></p>"""
    return _response(_page(body, settings.app_name), "'none'")
