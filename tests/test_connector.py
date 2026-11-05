"""The connector end to end, with Shopify, Cloudflare and tenants mocked."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.crypto import decrypt, encrypt
from app.db import SessionLocal
from app.models import Product, Store, StoreStatus, SyncState, WebhookDelivery
from app.security import verify
from app.services import events, sync
from tests.conftest import SECRET, FakeRemote, auth, connect, make_tenant

PRODUCT = {"title": "Zaatar", "price": "4.50", "currency": "USD",
           "images": ["https://cdn.tenant.test/z.jpg"], "available": True}


def drain(settings: Settings, http: httpx.Client) -> int:
    return sync.drain_once(SessionLocal, settings, http)


def deliveries(db: Session) -> list[WebhookDelivery]:
    db.expire_all()
    return list(db.scalars(select(WebhookDelivery).order_by(WebhookDelivery.created_at)))


# --- Off and on ------------------------------------------------------------------


def test_shopify_routes_are_absent_until_configured(db: Session) -> None:
    from app.main import create_app

    plain = create_app(get_settings(), worker=False)
    with TestClient(plain) as c:
        assert c.get("/shopify/callback").status_code == 404


def test_production_refuses_unsafe_configuration() -> None:
    prod = get_settings().model_copy(update={"app_env": "production"})
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        prod.enforce_production_safety()


# --- Tenants ----------------------------------------------------------------------


def test_every_route_needs_a_tenant_key(client: TestClient) -> None:
    assert client.get("/v1/stores/x").status_code == 401
    assert client.get("/v1/stores/x", headers=auth("sc_live_nope")).status_code == 401


def test_a_tenant_never_sees_another_tenants_store(
    client: TestClient, db: Session, settings: Settings
) -> None:
    _, key_a, _ = make_tenant(db, settings, "alpha")
    _, key_b, _ = make_tenant(db, settings, "beta")
    connect(client, key_a, "alpha", ref="shop-1")
    assert client.get("/v1/stores/shop-1", headers=auth(key_a)).status_code == 200
    assert client.get("/v1/stores/shop-1", headers=auth(key_b)).status_code == 404
    assert client.put("/v1/stores/shop-1/products/p1", headers=auth(key_b),
                      json=PRODUCT).status_code == 404


# --- Connecting ---------------------------------------------------------------------


@pytest.mark.parametrize("shop", ["evil.com", "x.myshopify.com.evil.com"])
def test_connect_refuses_anything_but_a_myshopify_address(
    client: TestClient, db: Session, settings: Settings, shop: str
) -> None:
    _, key, _ = make_tenant(db, settings, "alpha")
    r = client.post("/v1/stores/s/connect", headers=auth(key),
                    json={"shop": shop, "return_url": "https://alpha.tenant.test/x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_shop"


def test_connect_refuses_a_return_url_off_the_allow_list(
    client: TestClient, db: Session, settings: Settings
) -> None:
    _, key, _ = make_tenant(db, settings, "alpha")
    r = client.post("/v1/stores/s/connect", headers=auth(key),
                    json={"shop": "souk", "return_url": "https://evil.test/steal"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_return_url"


def test_a_forged_callback_connects_nothing(
    client: TestClient, db: Session, settings: Settings
) -> None:
    from urllib.parse import parse_qs, urlparse

    from tests.conftest import signed

    _, key, _ = make_tenant(db, settings, "alpha")
    started = client.post("/v1/stores/s/connect", headers=auth(key),
                          json={"shop": "souk-test", "return_url": "https://alpha.tenant.test/d"})
    state = parse_qs(urlparse(started.json()["authorize_url"]).query)["state"][0]
    back = client.get("/shopify/callback", follow_redirects=False, params=signed(
        {"code": "c", "shop": "souk-test.myshopify.com", "state": state}, secret="wrong"))
    assert back.headers["location"] == "https://alpha.tenant.test/d?shopify=bad_hmac"
    db.expire_all()
    assert db.scalars(select(Store)).one().status is StoreStatus.PENDING


def test_connecting_encrypts_the_token_subscribes_webhooks_and_tells_the_tenant(
    client: TestClient, db: Session, settings: Settings, remote: FakeRemote
) -> None:
    _, key, _ = make_tenant(db, settings, "alpha")
    back = connect(client, key, "alpha")
    assert back.status_code == 307
    assert back.headers["location"] == "https://alpha.tenant.test/dashboard/shop-1?shopify=connected"
    store = db.scalars(select(Store)).one()
    assert store.access_token_enc and "shpat_plain" not in store.access_token_enc
    assert decrypt(settings, store.access_token_enc) == "shpat_plain"
    topics = sorted(str(v["variables"]["topic"]) for v in remote.gql("webhookSubscriptionCreate"))
    assert topics == ["APP_UNINSTALLED", "SHOP_UPDATE"]
    status = client.get("/v1/stores/shop-1", headers=auth(key)).json()
    assert status["status"] == "CONNECTED"
    assert status["storefront_url"] == "https://souk.example.com"
    assert "token" not in json.dumps(status)
    assert [d.event for d in deliveries(db)] == ["store.connected"]


def test_one_shopify_store_belongs_to_one_shop(
    client: TestClient, db: Session, settings: Settings
) -> None:
    _, key_a, _ = make_tenant(db, settings, "alpha")
    _, key_b, _ = make_tenant(db, settings, "beta")
    connect(client, key_a, "alpha")
    back = connect(client, key_b, "beta")
    assert back.headers["location"].endswith("shopify=shop_taken")


# --- Products -----------------------------------------------------------------------


def test_products_are_created_once_then_updated_and_unchanged_ones_skipped(
    client: TestClient, db: Session, settings: Settings, http: httpx.Client, remote: FakeRemote
) -> None:
    _, key, _ = make_tenant(db, settings, "alpha")
    connect(client, key, "alpha")
    r = client.put("/v1/stores/shop-1/products/p1", headers=auth(key), json=PRODUCT)
    assert r.status_code == 202 and r.json()["state"] == "QUEUED"
    assert drain(settings, http) == 1
    sent = remote.gql("productSet")
    assert len(sent) == 1 and "id" not in sent[0]["variables"]["input"]  # type: ignore[index,operator]
    assert remote.gql("publishablePublish")

    got = client.get("/v1/stores/shop-1/products/p1", headers=auth(key)).json()
    assert got["buy_url"] == "https://souk.example.com/products/zaatar"

    client.put("/v1/stores/shop-1/products/p1", headers=auth(key), json=PRODUCT)
    drain(settings, http)
    assert len(remote.gql("productSet")) == 1  # nothing Shopify sees changed

    client.put("/v1/stores/shop-1/products/p1", headers=auth(key),
               json={**PRODUCT, "price": "7.25"})
    drain(settings, http)
    last = remote.gql("productSet")[-1]["variables"]["input"]  # type: ignore[index]
    assert last["id"] == "gid://shopify/Product/1"  # type: ignore[index]
    assert last["variants"][0]["price"] == "7.25"  # type: ignore[index]
    assert "product.synced" in [d.event for d in deliveries(db)]


def test_a_price_in_another_currency_is_never_pushed(
    client: TestClient, db: Session, settings: Settings, http: httpx.Client, remote: FakeRemote
) -> None:
    _, key, _ = make_tenant(db, settings, "alpha")
    connect(client, key, "alpha")
    client.put("/v1/stores/shop-1/products/p1", headers=auth(key),
               json={**PRODUCT, "currency": "LBP"})
    drain(settings, http)
    assert remote.gql("productSet") == []
    product = db.scalars(select(Product)).one()
    db.refresh(product)
    assert product.state is SyncState.SKIPPED and "LBP" in (product.last_error or "")
    assert "product.failed" in [d.event for d in deliveries(db)]


def test_bulk_and_delete(
    client: TestClient, db: Session, settings: Settings, http: httpx.Client, remote: FakeRemote
) -> None:
    _, key, _ = make_tenant(db, settings, "alpha")
    connect(client, key, "alpha")
    r = client.post("/v1/stores/shop-1/products/bulk", headers=auth(key), json={
        "products": [{**PRODUCT, "product_ref": "a"}, {**PRODUCT, "product_ref": "b"}]})
    assert r.json() == {"queued": 2}
    assert drain(settings, http) == 2
    assert client.delete("/v1/stores/shop-1/products/a", headers=auth(key)).status_code == 202
    drain(settings, http)
    assert remote.gql("productDelete")


def test_a_failing_push_is_retried_with_the_error_kept(
    client: TestClient, db: Session, settings: Settings
) -> None:
    _, key, _ = make_tenant(db, settings, "alpha")
    connect(client, key, "alpha")
    client.put("/v1/stores/shop-1/products/p1", headers=auth(key), json=PRODUCT)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))) as down:
        assert drain(settings, down) == 0
    db.expire_all()
    store = db.scalars(select(Store)).one()
    assert store.last_error == "the store's access token was refused"


# --- Shopify webhooks ----------------------------------------------------------------


def _hook(client: TestClient, topic: str, secret: str = SECRET) -> httpx.Response:
    body = b'{"id":1}'
    digest = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return client.post("/shopify/webhooks", content=body, headers={
        "X-Shopify-Hmac-Sha256": digest, "X-Shopify-Topic": topic,
        "X-Shopify-Shop-Domain": "souk-test.myshopify.com"})


def test_uninstall_removes_buy_links_and_tells_the_tenant(
    client: TestClient, db: Session, settings: Settings, http: httpx.Client
) -> None:
    _, key, _ = make_tenant(db, settings, "alpha")
    connect(client, key, "alpha")
    client.put("/v1/stores/shop-1/products/p1", headers=auth(key), json=PRODUCT)
    drain(settings, http)
    assert _hook(client, "app/uninstalled").status_code == 200
    got = client.get("/v1/stores/shop-1/products/p1", headers=auth(key)).json()
    assert got["buy_url"] is None
    assert client.get("/v1/stores/shop-1", headers=auth(key)).json()["status"] == "UNINSTALLED"
    assert "store.updated" in [d.event for d in deliveries(db)]


def test_an_unsigned_shopify_webhook_is_refused(
    client: TestClient, db: Session, settings: Settings
) -> None:
    _, key, _ = make_tenant(db, settings, "alpha")
    connect(client, key, "alpha")
    assert _hook(client, "app/uninstalled", secret="forged").status_code == 401


# --- Webhooks to tenants ----------------------------------------------------------


def test_deliveries_are_signed_with_the_tenants_secret_and_retried(
    client: TestClient, db: Session, settings: Settings, http: httpx.Client, remote: FakeRemote
) -> None:
    _, key, secret = make_tenant(db, settings, "alpha")
    connect(client, key, "alpha")
    remote.tenant_status = 500
    assert events.deliver_once(SessionLocal, settings, http) == 0
    pending = deliveries(db)[0]
    assert pending.attempts == 1 and pending.delivered_at is None

    remote.tenant_status = 200
    pending.next_attempt_at = pending.created_at
    db.commit()
    assert events.deliver_once(SessionLocal, settings, http) == 1
    sent = [r for r in remote.requests if r.url.host == "alpha.tenant.test"][-1]
    assert sent.headers["X-Connector-Event"] == "store.connected"
    assert verify(secret, sent.content, sent.headers["X-Connector-Signature"])
    assert not verify("whsec_other", sent.content, sent.headers["X-Connector-Signature"])
    assert json.loads(sent.content)["data"]["store_ref"] == "shop-1"


# --- Subdomains -----------------------------------------------------------------------


def test_a_subdomain_is_a_dns_only_cname_in_the_tenants_own_zone(
    client: TestClient, db: Session, settings: Settings, remote: FakeRemote
) -> None:
    tenant, key, _ = make_tenant(db, settings, "alpha")
    tenant.cloudflare_zone_id = "zone-alpha"
    tenant.cloudflare_token_enc = encrypt(settings, "cf-token")
    db.commit()
    connect(client, key, "alpha")

    for bad, code in (("www", "reserved_subdomain"), ("Bad_Name", "bad_subdomain")):
        r = client.post("/v1/stores/shop-1/subdomain", headers=auth(key), json={"subdomain": bad})
        assert r.status_code == 422 and r.json()["error"]["code"] == code

    r = client.post("/v1/stores/shop-1/subdomain", headers=auth(key), json={"subdomain": "souk"})
    assert r.status_code == 200, r.text
    assert r.json()["subdomain_hostname"] == "souk.alpha.example"
    created = next(q for q in remote.requests if q.url.host == "api.cloudflare.com")
    assert "/zones/zone-alpha/" in created.url.path
    assert created.headers["Authorization"] == "Bearer cf-token"
    assert json.loads(created.content) == {
        "type": "CNAME", "name": "souk.alpha.example", "content": "shops.myshopify.com",
        "proxied": False, "ttl": 1,
    }


def test_a_tenant_without_cloudflare_has_no_subdomains(
    client: TestClient, db: Session, settings: Settings
) -> None:
    _, key, _ = make_tenant(db, settings, "alpha")
    connect(client, key, "alpha")
    r = client.post("/v1/stores/shop-1/subdomain", headers=auth(key), json={"subdomain": "souk"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "subdomains_off"


def test_disconnecting_revokes_the_token_and_tells_the_tenant(
    client: TestClient, db: Session, settings: Settings, remote: FakeRemote
) -> None:
    _, key, _ = make_tenant(db, settings, "alpha")
    connect(client, key, "alpha")
    assert client.delete("/v1/stores/shop-1", headers=auth(key)).status_code == 204
    assert any(r.url.path == "/admin/api_permissions/current.json" for r in remote.requests)
    assert client.get("/v1/stores/shop-1", headers=auth(key)).status_code == 404
    assert deliveries(db)[-1].event == "store.disconnected"


def test_a_tenant_reads_its_own_settings(
    client: TestClient, db: Session, settings: Settings
) -> None:
    _, key, _ = make_tenant(db, settings, "alpha")
    info = client.get("/v1/tenant", headers=auth(key)).json()
    assert info == {"slug": "alpha", "name": "Alpha", "signup_url": None,
                    "subdomain_base": "alpha.example", "subdomains_available": False}
