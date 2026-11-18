"""Installing from Shopify (managed install): the token is kept, a website links it later."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Iterator

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_http
from app.config import Settings, get_settings
from app.main import create_app
from app.models import Installation, Store, StoreStatus
from tests.conftest import SECRET, FakeRemote, auth, make_tenant, signed

SHOP = "souk-test.myshopify.com"


def session_token(shop: str = SHOP, secret: str = SECRET, aud: str = "app-key") -> str:
    now = int(time.time())
    return jwt.encode({"iss": f"https://{shop}/admin", "dest": f"https://{shop}", "aud": aud,
                       "sub": "1", "exp": now + 60, "nbf": now, "iat": now}, secret,
                      algorithm="HS256")


def launch(token: str, shop: str = SHOP, secret: str = SECRET) -> dict[str, str]:
    return signed({"shop": shop, "timestamp": str(int(time.time())), "host": "x",
                   "embedded": "1", "id_token": token}, secret)


def exchanges(remote: FakeRemote) -> list[dict[str, str]]:
    return [json.loads(r.content) for r in remote.requests
            if r.url.path == "/admin/oauth/access_token"]


def test_an_install_from_shopify_keeps_the_token_and_the_website_links_it_without_a_grant(
    client: TestClient, db: Session, settings: Settings, remote: FakeRemote
) -> None:
    page = client.get("/", params=launch(session_token()))
    assert page.status_code == 200 and "Installed" in page.text
    sent = exchanges(remote)[0]
    assert sent["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert sent["requested_token_type"].endswith("offline-access-token")
    installed = db.scalars(select(Installation)).one()
    assert installed.access_token_enc and "shpat_plain" not in installed.access_token_enc

    _, key, _ = make_tenant(db, settings, "site")
    r = client.post("/v1/stores/shop-1/connect", headers=auth(key), json={
        "shop": SHOP, "return_url": "https://site.tenant.test/admin/shopify"})
    assert r.status_code == 200, r.text
    # Straight back to the website: no Shopify grant screen, no second code exchange.
    assert r.json()["authorize_url"] == "https://site.tenant.test/admin/shopify?shopify=connected"
    assert len(exchanges(remote)) == 1
    store = db.scalars(select(Store)).one()
    db.refresh(installed)
    assert store.status is StoreStatus.CONNECTED and store.shop_domain == SHOP
    assert installed.claimed_at is not None
    assert "Connected to Site" in client.get("/", params=launch(session_token())).text


def test_a_forged_or_foreign_session_token_installs_nothing(
    client: TestClient, db: Session, remote: FakeRemote
) -> None:
    for token in (session_token(secret="not-the-secret"), session_token(aud="other-app"),
                  session_token(shop="other.myshopify.com")):
        page = client.get("/", params=launch(token))
        assert "Installed" not in page.text
    assert exchanges(remote) == []
    assert db.scalars(select(Installation)).first() is None


def _hook(client: TestClient, topic: str, secret: str = SECRET) -> httpx.Response:
    body = b'{"id":1}'
    digest = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return client.post("/shopify/webhooks", content=body, headers={
        "X-Shopify-Hmac-Sha256": digest, "X-Shopify-Topic": topic,
        "X-Shopify-Shop-Domain": SHOP})


def test_uninstalling_before_any_website_linked_the_store_forgets_its_token(
    client: TestClient, db: Session
) -> None:
    client.get("/", params=launch(session_token()))
    assert _hook(client, "app/uninstalled").status_code == 200
    assert db.scalars(select(Installation)).first() is None


@pytest.fixture
def two_apps(settings: Settings, http: httpx.Client) -> Iterator[TestClient]:
    both = settings.model_copy(update={"shopify_extra_secrets": "old-app-secret"})
    app = create_app(both, worker=False)
    app.dependency_overrides[get_settings] = lambda: both
    app.dependency_overrides[get_http] = lambda: http
    with TestClient(app) as test_client:
        yield test_client


def test_the_earlier_apps_webhooks_are_still_accepted_while_stores_move(
    client: TestClient, two_apps: TestClient
) -> None:
    assert _hook(client, "shop/update", "old-app-secret").status_code == 401
    assert _hook(two_apps, "shop/update", "old-app-secret").status_code == 200
    assert _hook(two_apps, "shop/update", "nobody").status_code == 401
