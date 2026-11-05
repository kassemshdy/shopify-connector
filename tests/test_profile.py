from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import SessionLocal
from app.models import Job, Store
from app.services import profile, sync
from tests.conftest import FakeRemote, auth, connect, make_tenant

SCHEMA = json.dumps([
    {"name": "theme_info"},
    {"name": "Logo", "settings": [{"type": "image_picker", "id": "logo"}]},
    {"name": "Social", "settings": [
        {"type": "text", "id": "social_instagram_link"},
        {"type": "text", "id": "social_facebook_link"},
    ]},
])
DATA = "/*\n * Shopify writes this comment.\n */\n" + json.dumps(
    {"current": "Default", "presets": {"Default": {"colors_text": "#121212"}}}
)


class ThemeRemote(FakeRemote):
    """FakeRemote, plus a store with a Dawn-like theme and a Files area."""

    def __init__(self) -> None:
        super().__init__()
        self.deny_theme_write = False
        self.written: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("graphql.json"):
            body = json.loads(request.content)
            query = body["query"]
            if "MainTheme" in query:
                self.requests.append(request)
                return httpx.Response(200, json={"data": {"themes": {"nodes": [{
                    "id": "gid://shopify/OnlineStoreTheme/1", "name": "Dawn",
                    "files": {"nodes": [
                        {"filename": "config/settings_data.json", "body": {"content": DATA}},
                        {"filename": "config/settings_schema.json", "body": {"content": SCHEMA}},
                    ]}}]}}})
            if "FileCreate" in query:
                self.requests.append(request)
                return httpx.Response(200, json={"data": {"fileCreate": {"files": [{
                    "id": "gid://shopify/MediaImage/9", "fileStatus": "READY",
                    "image": {"url": "https://cdn.shopify.com/s/files/1/0/files/logo.jpg?v=1"}}],
                    "userErrors": []}}})
            if "ThemeUpsert" in query:
                self.requests.append(request)
                if self.deny_theme_write:
                    return httpx.Response(200, json={"data": {"themeFilesUpsert": {
                        "upsertedThemeFiles": None,
                        "userErrors": [{"field": None, "message": "Access denied",
                                        "code": "ACCESS_DENIED"}]}}})
                self.written = body["variables"]["files"][0]["body"]["value"]
                return httpx.Response(200, json={"data": {"themeFilesUpsert": {
                    "upsertedThemeFiles": [{"filename": "config/settings_data.json"}],
                    "userErrors": []}}})
        return super().__call__(request)


@pytest.fixture
def remote() -> ThemeRemote:
    return ThemeRemote()


@pytest.fixture
def http(remote: ThemeRemote) -> Iterator[httpx.Client]:
    with httpx.Client(transport=httpx.MockTransport(remote)) as client:
        yield client


PROFILE = {
    "name": "Boutique Lilac",
    "logo_url": "https://www.example.test/media/logo.jpg",
    "social": {"instagram": "https://instagram.com/lilac", "tiktok": "https://tiktok.com/@lilac"},
}


def _connected(client: TestClient, db: Session, settings: Settings, *, scopes: str) -> str:
    _, key, _ = make_tenant(db, settings, "alpha")
    connect(client, key, "alpha")
    store = db.scalars(select(Store)).one()
    store.scopes = scopes
    db.commit()
    return key


def test_a_store_connected_before_theme_access_is_asked_to_reconnect(
    client: TestClient, db: Session, settings: Settings
) -> None:
    key = _connected(client, db, settings, scopes="read_products,write_products")
    r = client.put("/v1/stores/shop-1/profile", headers=auth(key), json=PROFILE)
    assert r.status_code == 409 and r.json()["error"]["code"] == "reconnect_required"
    assert db.scalars(select(Job).where(Job.kind == profile.PROFILE)).first() is None


def test_the_logo_and_declared_social_links_go_into_the_live_theme(
    client: TestClient, db: Session, settings: Settings, http: httpx.Client, remote: ThemeRemote
) -> None:
    key = _connected(client, db, settings,
                     scopes="write_products,write_publications,write_themes,write_files")
    r = client.put("/v1/stores/shop-1/profile", headers=auth(key), json=PROFILE)
    assert r.status_code == 202, r.text
    sync.drain_once(SessionLocal, settings, http)

    assert remote.written is not None
    assert remote.written.startswith("/*\n * Shopify writes this comment.\n */\n")
    current = json.loads(remote.written.split("*/", 1)[1])["current"]
    assert current["logo"] == "shopify://shop_images/logo.jpg"
    assert current["social_instagram_link"] == "https://instagram.com/lilac"
    # The preset's own values are kept; a link the theme does not declare is not invented.
    assert current["colors_text"] == "#121212"
    assert "social_tiktok_link" not in current
    job = db.scalars(select(Job).where(Job.kind == profile.PROFILE)).one()
    db.refresh(job)
    assert job.done_at is not None and job.last_error is None


def test_without_shopifys_exemption_the_job_stops_once_with_the_reason(
    client: TestClient, db: Session, settings: Settings, http: httpx.Client, remote: ThemeRemote
) -> None:
    remote.deny_theme_write = True
    key = _connected(client, db, settings,
                     scopes="write_products,write_publications,write_themes,write_files")
    client.put("/v1/stores/shop-1/profile", headers=auth(key), json=PROFILE)
    sync.drain_once(SessionLocal, settings, http)

    job = db.scalars(select(Job).where(Job.kind == profile.PROFILE)).one()
    db.refresh(job)
    assert job.attempts == 1 and job.done_at is not None
    assert "exemption" in (job.last_error or "")
    store = db.scalars(select(Store)).one()
    db.refresh(store)
    assert "exemption" in (store.last_error or "")


def test_an_unchanged_profile_writes_nothing() -> None:
    values = {"social_instagram_link": "https://instagram.com/lilac"}
    data = json.dumps({"current": values})
    new, changed = profile.apply_settings(data, SCHEMA, values)
    assert changed == []
    assert json.loads(new)["current"] == values
