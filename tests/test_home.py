from __future__ import annotations

import time

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from tests.conftest import connect, make_tenant, signed

SHOP = "souk-test.myshopify.com"


def launch(shop: str = SHOP, age: int = 0) -> dict[str, str]:
    return signed({"shop": shop, "timestamp": str(int(time.time()) - age), "host": "x"})


def test_without_a_signed_launch_the_page_says_nothing_about_any_store(
    client: TestClient, db: Session, settings: Settings
) -> None:
    _, key, _ = make_tenant(db, settings, "site")
    connect(client, key, "site")
    for params in ({}, {"shop": SHOP}, {**launch(), "hmac": "0" * 64}, launch(age=3600)):
        page = client.get("/", params=params)
        assert page.status_code == 200
        assert "Site" not in page.text and SHOP not in page.text
        assert page.headers["content-security-policy"] == "frame-ancestors 'none';"


def test_a_connected_store_sees_its_website_and_sync_status(
    client: TestClient, db: Session, settings: Settings
) -> None:
    _, key, _ = make_tenant(db, settings, "site")
    connect(client, key, "site")
    page = client.get("/", params=launch())
    assert "Connected to Site" in page.text
    assert 'href="https://site.tenant.test"' in page.text
    assert f"https://{SHOP}" in page.headers["content-security-policy"]
    assert "admin.shopify.com" in page.headers["content-security-policy"]


def test_a_store_no_website_connected_is_told_where_to_connect(
    client: TestClient, db: Session, settings: Settings
) -> None:
    make_tenant(db, settings, "site")
    page = client.get("/", params=launch("other.myshopify.com"))
    assert "Not connected yet" in page.text
    assert "Site" not in page.text


def test_the_page_carries_the_apps_name(client: TestClient) -> None:
    page = client.get("/")
    assert "<title>Halyoon Connect</title>" in page.text
    assert "<h1>Halyoon Connect</h1>" in page.text


def test_the_privacy_and_support_pages_are_public_and_name_the_contact_when_set(
    client: TestClient, settings: Settings
) -> None:
    for path in ("/privacy", "/support"):
        page = client.get(path)
        assert page.status_code == 200 and settings.app_name in page.text
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert "mailto:" not in page.text
    assert "customer" in client.get("/privacy").text
