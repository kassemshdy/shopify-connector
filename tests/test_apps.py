"""One Shopify app per store: its own credentials for connect, callback, webhooks, launches."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import Conflict
from app.models import Installation, ShopifyApp, Store, StoreStatus
from app.services import apps
from tests.conftest import FakeRemote, auth, make_tenant, signed
from tests.test_operator import PASSWORD, _client, csrf, sign_in

SHOP = "own-shop.myshopify.com"
OWN_ID, OWN_SECRET = "own-client-id", "own-secret-value"


def _own_app(db: Session, settings: Settings) -> None:
    apps.add(db, settings, shop=SHOP, client_id=OWN_ID, secret=OWN_SECRET, name="Own")


def _start(client: TestClient, key: str) -> str:
    r = client.post("/v1/stores/shop-1/connect", headers=auth(key), json={
        "shop": SHOP, "return_url": "https://site.tenant.test/admin"})
    assert r.status_code == 200, r.text
    return str(r.json()["authorize_url"])


def test_a_store_with_its_own_app_connects_through_that_app(
    client: TestClient, db: Session, settings: Settings, remote: FakeRemote
) -> None:
    _own_app(db, settings)
    _, key, _ = make_tenant(db, settings, "site")
    url = _start(client, key)
    query = parse_qs(urlparse(url).query)
    assert query["client_id"] == [OWN_ID]
    params = {"code": "abc", "shop": SHOP, "state": query["state"][0], "timestamp": "1"}

    # Signed with the default app's secret: refused.
    refused = client.get("/shopify/callback", params=signed(params), follow_redirects=False)
    assert "connected" not in refused.headers.get("location", "")

    done = client.get("/shopify/callback", params=signed(params, OWN_SECRET),
                      follow_redirects=False)
    assert done.headers["location"].endswith("shopify=connected")
    sent = [json.loads(r.content) for r in remote.requests
            if r.url.path == "/admin/oauth/access_token"][-1]
    assert sent["client_id"] == OWN_ID and sent["client_secret"] == OWN_SECRET
    store = db.scalars(select(Store)).one()
    assert store.status is StoreStatus.CONNECTED


def test_other_stores_still_use_the_default_app(
    client: TestClient, db: Session, settings: Settings
) -> None:
    _own_app(db, settings)
    _, key, _ = make_tenant(db, settings, "site")
    r = client.post("/v1/stores/shop-2/connect", headers=auth(key), json={
        "shop": "other.myshopify.com", "return_url": "https://site.tenant.test/admin"})
    assert parse_qs(urlparse(r.json()["authorize_url"]).query)["client_id"] == ["app-key"]


def _hook(client: TestClient, secret: str) -> httpx.Response:
    body = b'{"id":1}'
    digest = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return client.post("/shopify/webhooks", content=body, headers={
        "X-Shopify-Hmac-Sha256": digest, "X-Shopify-Topic": "shop/update",
        "X-Shopify-Shop-Domain": SHOP})


def test_webhooks_for_a_store_are_checked_with_its_own_secret(
    client: TestClient, db: Session, settings: Settings
) -> None:
    _own_app(db, settings)
    assert _hook(client, OWN_SECRET).status_code == 200
    assert _hook(client, "someone-else").status_code == 401


def test_installing_a_stores_own_app_uses_its_credentials(
    client: TestClient, db: Session, settings: Settings, remote: FakeRemote
) -> None:
    _own_app(db, settings)
    now = int(time.time())
    token = jwt.encode({"dest": f"https://{SHOP}", "aud": OWN_ID, "exp": now + 60, "nbf": now,
                        "iat": now, "sub": "1"}, OWN_SECRET, algorithm="HS256")
    params = signed({"shop": SHOP, "timestamp": str(now), "host": "x", "id_token": token},
                    OWN_SECRET)
    page = client.get("/", params=params)
    assert "Installed" in page.text and OWN_ID in page.text
    sent = [json.loads(r.content) for r in remote.requests
            if r.url.path == "/admin/oauth/access_token"][-1]
    assert sent["client_id"] == OWN_ID
    assert db.scalars(select(Installation)).one().shop_domain == SHOP


def test_the_secret_is_stored_encrypted_and_duplicates_are_refused(
    db: Session, settings: Settings
) -> None:
    _own_app(db, settings)
    stored = db.scalars(select(ShopifyApp)).one()
    assert OWN_SECRET not in stored.secret_enc
    with pytest.raises(Conflict):
        _own_app(db, settings)


@pytest.fixture
def op(settings: Settings, http: httpx.Client) -> Iterator[TestClient]:
    from app.api import operator as operator_module

    operator_module._failures.clear()
    operator_module._all_failures.clear()
    with _client(settings.model_copy(update={"operator_password": PASSWORD}), http) as c:
        yield c


def test_the_operator_adds_and_removes_a_stores_app_without_showing_the_secret(
    op: TestClient, db: Session
) -> None:
    sign_in(op)
    page = op.get("/operator/apps")
    r = op.post("/operator/apps", data={"csrf": csrf(page), "shop": SHOP, "name": "Own",
                                        "client_id": OWN_ID, "secret": OWN_SECRET})
    assert r.status_code == 200 and OWN_SECRET not in r.text and OWN_ID in r.text
    app = db.scalars(select(ShopifyApp)).one()
    op.post(f"/operator/apps/{app.id}/delete", data={"csrf": csrf(r)})
    db.expire_all()
    assert db.scalars(select(ShopifyApp)).first() is None
