"""The operator page: register websites, rotate their keys, see every store.

Mounted only when ``OPERATOR_PASSWORD`` is set; otherwise every path under
/operator is a 404. One password, because the operator is the company running
the connector, not its tenants.

- The session is a signed cookie (HttpOnly, SameSite=Strict, Secure on https,
  path /operator) that expires after twelve hours and dies when the password
  changes, since it carries a fingerprint of it.
- Every form that changes something also carries a token derived from the
  session, so a page elsewhere cannot submit one.
- Five wrong passwords from one address in fifteen minutes block that address
  for the rest of the window.
- A new key or webhook secret is shown once, on the response to the action
  that made it, and is never stored in plaintext. The Cloudflare token is
  write-only: the page says whether one is set, never what it is.
- No page can be framed, and none is cached.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections import defaultdict, deque
from html import escape
from typing import Annotated, Any
from urllib.parse import parse_qs

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AppSettings, DbSession
from app.config import Settings
from app.errors import NotFound
from app.models import Product, Store, StoreStatus, SyncState, Tenant, WebhookDelivery
from app.services import tenants

router = APIRouter(prefix="/operator", include_in_schema=False)

COOKIE = "op_session"
SESSION_SECONDS = 12 * 3600
MAX_FAILURES, FAILURE_WINDOW = 5, 15 * 60
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,58}$")
_failures: dict[str, deque[float]] = defaultdict(deque)


# -- guards -------------------------------------------------------------------

def enabled(settings: AppSettings) -> Settings:
    if not settings.operator_password:
        raise NotFound("not_found", "Not found.")
    return settings


def _fingerprint(settings: Settings) -> str:
    assert settings.operator_password
    return hashlib.sha256(settings.operator_password.encode()).hexdigest()[:16]


def _session(request: Request, settings: Settings) -> str | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        claims = jwt.decode(token, settings.secret_key, algorithms=["HS256"], audience="operator")
    except jwt.PyJWTError:
        return None
    return token if claims.get("pw") == _fingerprint(settings) else None


def _csrf(settings: Settings, session: str) -> str:
    return hmac.new(settings.secret_key.encode(), session.encode(), hashlib.sha256).hexdigest()


async def _form(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode()
    return {k: v[-1].strip() for k, v in parse_qs(raw, keep_blank_values=True).items()}


class Operator:
    def __init__(self, session: str, settings: Settings) -> None:
        self.session, self.csrf = session, _csrf(settings, session)


Enabled = Annotated[Settings, Depends(enabled)]


def operator(request: Request, settings: Enabled) -> Operator:
    session = _session(request, settings)
    if session is None:
        raise HTTPException(status_code=303, headers={"Location": "/operator/login"})
    return Operator(session, settings)


Op = Annotated[Operator, Depends(operator)]


async def checked_form(request: Request, op: Operator) -> dict[str, str]:
    form = await _form(request)
    if not hmac.compare_digest(form.get("csrf", ""), op.csrf):
        raise HTTPException(status_code=403, detail="Form expired; reload the page.")
    return form


# -- rendering ----------------------------------------------------------------

STYLE = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  margin: 0; background: #f6f6f7; color: #202223; }
header { background: #1a1c1d; color: #fff; padding: 12px 24px; display: flex;
  justify-content: space-between; align-items: center; }
header a, header button { color: #fff; }
main { max-width: 1100px; margin: 24px auto; padding: 0 16px; }
section { background: #fff; border-radius: 12px; padding: 20px 24px; margin-bottom: 20px;
  box-shadow: 0 1px 3px rgba(0,0,0,.08); overflow-x: auto; }
h1 { font-size: 22px; } h2 { font-size: 17px; margin-top: 0; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #e1e3e5;
  vertical-align: top; }
th { color: #6d7175; font-weight: 600; }
label { display: block; margin: 10px 0 4px; font-weight: 600; font-size: 14px; }
input[type=text], input[type=password], input[type=url], textarea {
  width: 100%; box-sizing: border-box; padding: 8px 10px; border: 1px solid #c9cccf;
  border-radius: 8px; font: inherit; }
textarea { min-height: 64px; }
.hint { color: #6d7175; font-size: 13px; margin: 2px 0 0; }
button, a.button { display: inline-block; padding: 8px 14px; border-radius: 8px; border: 0;
  background: #008060; color: #fff; font: inherit; cursor: pointer; text-decoration: none; }
button.secondary { background: #e4e5e7; color: #202223; }
button.danger { background: #d82c0d; }
header button { background: transparent; border: 1px solid #fff; }
.row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
.ok { color: #007f5f; } .bad { color: #d82c0d; } .muted { color: #6d7175; }
.secret { background: #fff4e4; border: 1px solid #ffc453; border-radius: 10px; padding: 14px 16px; }
code { background: #f1f2f3; padding: 2px 6px; border-radius: 6px; word-break: break-all; }
.error { background: #fff4f4; border: 1px solid #fead9a; border-radius: 10px; padding: 10px 14px; }
"""


def _page(title: str, body: str, op: Operator | None = None) -> HTMLResponse:
    bar = (
        '<form method="post" action="/operator/logout" style="margin:0">'
        f'<input type="hidden" name="csrf" value="{op.csrf}">'
        '<button>Sign out</button></form>' if op else ""
    )
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>{escape(title)} · Connector operator</title>
<style>{STYLE}</style></head><body>
<header><a href="/operator" style="text-decoration:none"><strong>Shopify Connector</strong>
· operator</a>{bar}</header><main>{body}</main></body></html>"""
    return HTMLResponse(html, headers={
        "Content-Security-Policy": "frame-ancestors 'none'; default-src 'self'; "
                                   "style-src 'unsafe-inline'",
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
    })


def _hidden(op: Operator) -> str:
    return f'<input type="hidden" name="csrf" value="{op.csrf}">'


def _when(value: Any) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else '<span class="muted">—</span>'


def _shown_once(label: str, value: str, extra: str = "") -> str:
    return (
        f'<section class="secret"><h2>{escape(label)}</h2>'
        "<p>Copy it now and give it to the website's developers. It will not be shown again; "
        "only its hash or ciphertext is kept.</p>"
        f"<p><code>{escape(value)}</code></p>{extra}</section>"
    )


# -- sign in ------------------------------------------------------------------

def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _blocked(ip: str) -> bool:
    attempts = _failures[ip]
    while attempts and time.time() - attempts[0] > FAILURE_WINDOW:
        attempts.popleft()
    return len(attempts) >= MAX_FAILURES


def _login_page(error: str = "") -> HTMLResponse:
    note = f'<p class="error">{escape(error)}</p>' if error else ""
    return _page("Sign in", f"""<section style="max-width:420px;margin:40px auto">
<h1>Operator sign in</h1>{note}
<form method="post" action="/operator/login">
<label for="password">Password</label>
<input type="password" id="password" name="password" autocomplete="current-password" required>
<div class="row"><button>Sign in</button></div></form></section>""")


@router.get("/login")
def login_form(settings: Enabled) -> HTMLResponse:
    return _login_page()


@router.post("/login")
async def login(request: Request, settings: Enabled) -> Response:
    ip = _client(request)
    if _blocked(ip):
        return _login_page("Too many attempts. Try again in fifteen minutes.")
    password = (await _form(request)).get("password", "")
    assert settings.operator_password
    if not hmac.compare_digest(password.encode(), settings.operator_password.encode()):
        _failures[ip].append(time.time())
        return _login_page("Wrong password.")
    _failures.pop(ip, None)
    token = jwt.encode(
        {"aud": "operator", "pw": _fingerprint(settings),
         "exp": int(time.time()) + SESSION_SECONDS},
        settings.secret_key, algorithm="HS256",
    )
    response = RedirectResponse("/operator", status_code=303)
    response.set_cookie(
        COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="strict",
        secure=settings.public_base_url.startswith("https://"), path="/operator",
    )
    return response


@router.post("/logout")
async def logout(request: Request, op: Op) -> Response:
    await checked_form(request, op)
    response = RedirectResponse("/operator/login", status_code=303)
    response.delete_cookie(COOKIE, path="/operator")
    return response


# -- websites -----------------------------------------------------------------

def _store_counts(db: Session) -> dict[Any, tuple[int, int]]:
    rows = db.execute(
        select(Store.tenant_id, Store.status, func.count()).group_by(Store.tenant_id, Store.status)
    ).tuples()
    out: dict[Any, tuple[int, int]] = {}
    for tenant_id, status, count in rows:
        connected, total = out.get(tenant_id, (0, 0))
        out[tenant_id] = (connected + (count if status is StoreStatus.CONNECTED else 0),
                          total + count)
    return out


def _delivery_counts(db: Session) -> dict[Any, tuple[int, int]]:
    pending = dict(db.execute(
        select(WebhookDelivery.tenant_id, func.count())
        .where(WebhookDelivery.delivered_at.is_(None), WebhookDelivery.gave_up_at.is_(None))
        .group_by(WebhookDelivery.tenant_id)).tuples().all())
    failed = dict(db.execute(
        select(WebhookDelivery.tenant_id, func.count())
        .where(WebhookDelivery.gave_up_at.is_not(None))
        .group_by(WebhookDelivery.tenant_id)).tuples().all())
    return {k: (pending.get(k, 0), failed.get(k, 0)) for k in set(pending) | set(failed)}


@router.get("")
def index(db: DbSession, op: Op) -> HTMLResponse:
    sites = db.scalars(select(Tenant).order_by(Tenant.name)).all()
    stores, deliveries = _store_counts(db), _delivery_counts(db)
    rows = ""
    for t in sites:
        connected, total = stores.get(t.id, (0, 0))
        pending, failed = deliveries.get(t.id, (0, 0))
        state = ('<span class="ok">active</span>' if t.active
                 else '<span class="bad">disabled</span>')
        cf = ("yes" if t.cloudflare_token_enc and t.cloudflare_zone_id
              else '<span class="muted">no</span>')
        hooks = f"{pending} waiting"
        if failed:
            hooks += f', <span class="bad">{failed} failed</span>'
        rows += (
            f'<tr><td><a href="/operator/sites/{escape(t.slug)}">{escape(t.name)}</a><br>'
            f'<span class="muted">{escape(t.slug)}</span></td><td>{state}</td>'
            f"<td>{connected} / {total}</td><td>{cf}</td><td>{hooks}</td>"
            f'<td><code>{escape(t.api_key_prefix)}…</code></td></tr>'
        )
    if not rows:
        rows = '<tr><td colspan="6" class="muted">No websites yet.</td></tr>'
    return _page("Websites", f"""<h1>Websites</h1>
<section><table><tr><th>Website</th><th>State</th><th>Stores connected / all</th>
<th>Subdomains</th><th>Webhooks</th><th>Key</th></tr>{rows}</table>
<div class="row"><a class="button" href="/operator/sites/new">Add a website</a></div></section>""",
        op)


def _origins(raw: str) -> list[str]:
    return [o.rstrip("/") for o in re.split(r"[\s,]+", raw) if o]


def _validate(form: dict[str, str], settings: Settings, *, new: bool) -> list[str]:
    errors = []
    https_only = settings.app_env == "production"
    if new and not SLUG.match(form.get("slug", "")):
        errors.append("The slug takes 2 to 59 lowercase letters, digits or hyphens.")
    if not form.get("name"):
        errors.append("A name is required.")
    origins = _origins(form.get("origins", ""))
    if not origins:
        errors.append("At least one website address is required.")
    for value in origins + [u for u in (form.get("webhook_url"), form.get("signup_url")) if u]:
        if not value.startswith("https://") and (https_only or not value.startswith("http://")):
            errors.append(f"{value} must start with https://.")
    return errors


def _site_form(op: Operator, values: dict[str, str], *, new: bool, errors: list[str]) -> str:
    v = {k: escape(values.get(k, "")) for k in
         ("slug", "name", "origins", "webhook_url", "subdomain_base", "signup_url")}
    note = "".join(f'<p class="error">{escape(e)}</p>' for e in errors)
    slug_field = (
        '<label for="slug">Slug</label>'
        f'<input type="text" id="slug" name="slug" value="{v["slug"]}" required>'
        '<p class="hint">Lowercase, fixed once created, e.g. <code>janoubona</code>.</p>'
        if new else ""
    )
    return f"""{note}<form method="post">{_hidden(op)}{slug_field}
<label for="name">Name</label>
<input type="text" id="name" name="name" value="{v["name"]}" required>
<label for="origins">Website addresses</label>
<textarea id="origins" name="origins">{v["origins"]}</textarea>
<p class="hint">One per line. A connect may only return to these, e.g.
<code>https://janoubona.net</code>.</p>
<label for="webhook_url">Webhook address</label>
<input type="url" id="webhook_url" name="webhook_url" value="{v["webhook_url"]}">
<p class="hint">Where the connector sends signed store and product events.</p>
<label for="subdomain_base">Subdomain base</label>
<input type="text" id="subdomain_base" name="subdomain_base" value="{v["subdomain_base"]}">
<p class="hint">e.g. <code>janoubona.net</code> gives stores <code>name.janoubona.net</code>.
Needs the Cloudflare zone below.</p>
<label for="signup_url">Shopify sign-up link</label>
<input type="url" id="signup_url" name="signup_url" value="{v["signup_url"]}">
<p class="hint">Shown to shops without a store; may be a partner referral link.</p>
<div class="row"><button>{"Add website" if new else "Save"}</button></div></form>"""


def _apply(tenant: Tenant, form: dict[str, str]) -> None:
    tenant.name = form["name"]
    tenant.return_origins = " ".join(_origins(form.get("origins", "")))
    for field in ("webhook_url", "subdomain_base", "signup_url"):
        setattr(tenant, field, form.get(field) or None)


@router.get("/sites/new")
def new_site_form(op: Op) -> HTMLResponse:
    return _page("Add a website", "<h1>Add a website</h1><section>"
                 + _site_form(op, {}, new=True, errors=[]) + "</section>", op)


@router.post("/sites/new")
async def new_site(
    request: Request, db: DbSession, settings: AppSettings, op: Op
) -> HTMLResponse:
    form = await checked_form(request, op)
    errors = _validate(form, settings, new=True)
    if not errors and db.scalars(select(Tenant).where(Tenant.slug == form["slug"])).first():
        errors.append("That slug is taken.")
    if errors:
        return _page("Add a website", "<h1>Add a website</h1><section>"
                     + _site_form(op, form, new=True, errors=errors) + "</section>", op)
    tenant, key, secret = tenants.create(
        db, settings, slug=form["slug"], name=form["name"],
        return_origins=_origins(form["origins"]),
    )
    _apply(tenant, form)
    db.commit()
    body = (
        f"<h1>{escape(tenant.name)} added</h1>"
        + _shown_once("API key", key)
        + _shown_once("Webhook secret", secret)
        + f'<p><a class="button" href="/operator/sites/{escape(tenant.slug)}">'
        + "Open the website</a></p>"
    )
    return _page("Website added", body, op)


def _tenant(db: Session, slug: str) -> Tenant:
    tenant = db.scalars(select(Tenant).where(Tenant.slug == slug)).first()
    if tenant is None:
        raise NotFound("not_found", "Not found.")
    return tenant


def _delivery_state(h: WebhookDelivery) -> str:
    if h.delivered_at:
        return '<span class="ok">delivered</span>'
    return '<span class="bad">gave up</span>' if h.gave_up_at else "waiting"


def _site_page(db: Session, op: Operator, tenant: Tenant, *, top: str = "",
               form: dict[str, str] | None = None, errors: list[str] | None = None) -> HTMLResponse:
    values = form or {
        "name": tenant.name, "origins": "\n".join(tenant.origins),
        "webhook_url": tenant.webhook_url or "", "subdomain_base": tenant.subdomain_base or "",
        "signup_url": tenant.signup_url or "",
    }
    counts: dict[Any, dict[SyncState, int]] = defaultdict(dict)
    for store_id, state, count in db.execute(
        select(Product.store_id, Product.state, func.count())
        .join(Store, Store.id == Product.store_id).where(Store.tenant_id == tenant.id)
        .group_by(Product.store_id, Product.state)
    ).tuples():
        counts[store_id][state] = count
    store_rows = ""
    for s in db.scalars(select(Store).where(Store.tenant_id == tenant.id)
                        .order_by(Store.connected_at.desc().nullslast())).all():
        c = counts[s.id]
        status = {StoreStatus.CONNECTED: '<span class="ok">connected</span>',
                  StoreStatus.PENDING: '<span class="muted">pending</span>',
                  StoreStatus.UNINSTALLED: '<span class="bad">uninstalled</span>'}[s.status]
        failed = c.get(SyncState.FAILED, 0)
        host = (f"{s.subdomain}.{tenant.subdomain_base}"
                if s.subdomain and tenant.subdomain_base else "")
        store_rows += (
            f"<tr><td>{escape(s.shop_domain or '—')}<br><span class='muted'>"
            f"{escape(host)}"
            f"</span></td><td>{escape(s.external_ref)}</td><td>{status}</td>"
            f"<td>{c.get(SyncState.SYNCED, 0)}</td><td>{c.get(SyncState.QUEUED, 0)}</td>"
            f"<td>{f'<span class=bad>{failed}</span>' if failed else 0}</td>"
            f"<td>{_when(s.last_sync_at)}</td>"
            f"<td>{escape(s.last_error or '')}</td></tr>"
        )
    if not store_rows:
        store_rows = '<tr><td colspan="8" class="muted">No stores yet.</td></tr>'
    hooks = db.scalars(select(WebhookDelivery).where(WebhookDelivery.tenant_id == tenant.id)
                       .order_by(WebhookDelivery.created_at.desc()).limit(10)).all()
    hook_rows = "".join(
        f"<tr><td>{escape(h.event)}</td><td>{_when(h.created_at)}</td><td>{h.attempts}</td>"
        f"<td>{_delivery_state(h)}</td>"
        f"<td>{escape(h.last_error or '')}</td></tr>" for h in hooks
    ) or '<tr><td colspan="5" class="muted">None yet.</td></tr>'
    cf_state = ("A token is set (it is never shown)." if tenant.cloudflare_token_enc
                else "No token set: stores get no subdomain.")
    others = db.scalars(select(Tenant).where(Tenant.id != tenant.id,
                                             Tenant.cloudflare_token_enc.is_not(None))).all()
    share = "".join(f'<option value="{escape(o.slug)}">{escape(o.name)}</option>' for o in others)
    share_form = (
        f'<form method="post" action="/operator/sites/{escape(tenant.slug)}/cloudflare/copy" '
        f'class="row">{_hidden(op)}<select name="source">{share}</select>'
        '<button class="secondary">Use this website\'s zone and token</button></form>'
        if others else ""
    )
    toggle = "Disable" if tenant.active else "Enable"
    slug = escape(tenant.slug)
    body = f"""{top}<h1>{escape(tenant.name)}</h1>
<section><h2>Stores</h2><table><tr><th>Shopify store</th><th>Site's shop id</th><th>Status</th>
<th>In store</th><th>Waiting</th><th>Failed</th><th>Last sent</th><th>Last problem</th></tr>
{store_rows}</table></section>
<section><h2>Settings</h2>{_site_form(op, values, new=False, errors=errors or [])}</section>
<section><h2>Cloudflare</h2><p>{cf_state}</p>
<form method="post" action="/operator/sites/{slug}/cloudflare">{_hidden(op)}
<label for="zone">Zone id</label>
<input type="text" id="zone" name="zone_id" value="{escape(tenant.cloudflare_zone_id or '')}">
<label for="token">API token</label>
<input type="password" id="token" name="token" autocomplete="off">
<p class="hint">Leave empty to keep the current token. An empty zone id removes both.</p>
<div class="row"><button>Save Cloudflare</button></div></form>{share_form}</section>
<section><h2>Keys</h2><p>API key starts <code>{escape(tenant.api_key_prefix)}…</code>.
Replacing it stops the old one at once; the website must be updated in the same minute.</p>
<div class="row">
<form method="post" action="/operator/sites/{slug}/rotate-key">{_hidden(op)}
<button class="secondary">Replace API key</button></form>
<form method="post" action="/operator/sites/{slug}/rotate-secret">{_hidden(op)}
<button class="secondary">Replace webhook secret</button></form>
<form method="post" action="/operator/sites/{slug}/active">{_hidden(op)}
<button class="{'danger' if tenant.active else ''}">{toggle} this website</button></form>
</div></section>
<section><h2>Recent webhooks</h2><table><tr><th>Event</th><th>Created</th><th>Attempts</th>
<th>State</th><th>Last error</th></tr>{hook_rows}</table></section>"""
    return _page(tenant.name, body, op)


@router.get("/sites/{slug}")
def site(slug: str, db: DbSession, op: Op) -> HTMLResponse:
    return _site_page(db, op, _tenant(db, slug))


@router.post("/sites/{slug}")
async def save_site(
    slug: str, request: Request, db: DbSession, settings: AppSettings,
    op: Op,
) -> Response:
    tenant = _tenant(db, slug)
    form = await checked_form(request, op)
    errors = _validate(form, settings, new=False)
    if errors:
        return _site_page(db, op, tenant, form=form, errors=errors)
    _apply(tenant, form)
    db.commit()
    return RedirectResponse(f"/operator/sites/{tenant.slug}", status_code=303)


@router.post("/sites/{slug}/cloudflare")
async def save_cloudflare(
    slug: str, request: Request, db: DbSession, settings: AppSettings,
    op: Op,
) -> Response:
    tenant = _tenant(db, slug)
    form = await checked_form(request, op)
    tenants.set_cloudflare(db, settings, tenant, zone_id=form.get("zone_id"),
                           token=form.get("token"))
    return RedirectResponse(f"/operator/sites/{tenant.slug}", status_code=303)


@router.post("/sites/{slug}/cloudflare/copy")
async def copy_cloudflare(
    slug: str, request: Request, db: DbSession, op: Op
) -> Response:
    tenant = _tenant(db, slug)
    source = _tenant(db, (await checked_form(request, op)).get("source", ""))
    tenant.cloudflare_zone_id = source.cloudflare_zone_id
    tenant.cloudflare_token_enc = source.cloudflare_token_enc
    db.commit()
    return RedirectResponse(f"/operator/sites/{tenant.slug}", status_code=303)


@router.post("/sites/{slug}/rotate-key")
async def rotate_key(
    slug: str, request: Request, db: DbSession, op: Op
) -> HTMLResponse:
    tenant = _tenant(db, slug)
    await checked_form(request, op)
    key = tenants.rotate_key(db, tenant)
    return _site_page(db, op, tenant, top=_shown_once("New API key", key))


@router.post("/sites/{slug}/rotate-secret")
async def rotate_secret(
    slug: str, request: Request, db: DbSession, settings: AppSettings,
    op: Op,
) -> HTMLResponse:
    tenant = _tenant(db, slug)
    await checked_form(request, op)
    secret = tenants.rotate_secret(db, settings, tenant)
    return _site_page(db, op, tenant, top=_shown_once("New webhook secret", secret))


@router.post("/sites/{slug}/active")
async def toggle_active(
    slug: str, request: Request, db: DbSession, op: Op
) -> Response:
    tenant = _tenant(db, slug)
    await checked_form(request, op)
    tenant.active = not tenant.active
    db.commit()
    return RedirectResponse(f"/operator/sites/{tenant.slug}", status_code=303)
