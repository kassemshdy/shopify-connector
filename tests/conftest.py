from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/connector_test"
)

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_http
from app.config import Settings, get_settings
from app.db import Base, SessionLocal, engine
from app.main import create_app
from app.models import Tenant
from app.services import tenants

SECRET = "shpss_test"


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    yield
    names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} CASCADE"))


@pytest.fixture
def settings() -> Settings:
    return get_settings().model_copy(
        update={
            "shopify_api_key": "app-key",
            "shopify_api_secret": SECRET,
            "encryption_key": Fernet.generate_key().decode(),
            "public_base_url": "https://connector.test",
            "secret_key": "s" * 40,
        }
    )


class FakeRemote:
    """Answers like Shopify, Cloudflare and a tenant's webhook endpoint."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.currency = "USD"
        self.tenant_status = 200

    def gql(self, word: str) -> list[dict[str, object]]:
        return [
            json.loads(r.content)
            for r in self.requests
            if r.url.path.endswith("graphql.json") and word.encode() in r.content
        ]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host.endswith(".tenant.test"):
            return httpx.Response(self.tenant_status)
        if path == "/admin/oauth/access_token":
            return httpx.Response(200, json={"access_token": "shpat_plain", "scope": "x"})
        if path == "/admin/api_permissions/current.json":
            return httpx.Response(200, json={})
        if host == "api.cloudflare.com":
            if request.method == "POST":
                return httpx.Response(200, json={"success": True, "result": {"id": "rec1"}})
            return httpx.Response(200, json={"success": True})
        query = json.loads(request.content)["query"]
        if "shop {" in query:
            return httpx.Response(200, json={"data": {"shop": {
                "currencyCode": self.currency, "primaryDomain": {"host": "souk.example.com"}}}})
        if "webhookSubscriptionCreate" in query:
            return httpx.Response(200, json={"data": {"webhookSubscriptionCreate": {
                "userErrors": []}}})
        if "productSet" in query:
            status = json.loads(request.content)["variables"]["input"]["status"]
            return httpx.Response(200, json={"data": {"productSet": {
                "product": {"id": "gid://shopify/Product/1", "handle": "zaatar",
                            "status": status,
                            "variants": {"nodes": [{"id": "gid://shopify/ProductVariant/1"}]}},
                "userErrors": []}}})
        if "publications" in query:
            return httpx.Response(200, json={"data": {"publications": {
                "nodes": [{"id": "pub1", "name": "Online Store"}]}}})
        if "publishablePublish" in query:
            return httpx.Response(200, json={"data": {"publishablePublish": {"userErrors": []}}})
        if "productDelete" in query:
            return httpx.Response(200, json={"data": {"productDelete": {"userErrors": []}}})
        return httpx.Response(400, json={"errors": [{"message": "unexpected"}]})


@pytest.fixture
def remote() -> FakeRemote:
    return FakeRemote()


@pytest.fixture
def http(remote: FakeRemote) -> Iterator[httpx.Client]:
    with httpx.Client(transport=httpx.MockTransport(remote)) as client:
        yield client


@pytest.fixture
def client(settings: Settings, http: httpx.Client) -> Iterator[TestClient]:
    app = create_app(settings, worker=False)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_http] = lambda: http
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def make_tenant(db: Session, settings: Settings, slug: str) -> tuple[Tenant, str, str]:
    return tenants.create(
        db, settings, slug=slug, name=slug.title(),
        return_origins=[f"https://{slug}.tenant.test"],
        webhook_url=f"https://{slug}.tenant.test/hooks", subdomain_base=f"{slug}.example",
    )


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def signed(params: dict[str, str], secret: str = SECRET) -> dict[str, str]:
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return {**params, "hmac": digest}


def connect(client: TestClient, key: str, slug: str, ref: str = "shop-1",
            shop: str = "souk-test.myshopify.com") -> httpx.Response:
    started = client.post(
        f"/v1/stores/{ref}/connect", headers=auth(key),
        json={"shop": shop, "return_url": f"https://{slug}.tenant.test/dashboard/{ref}"},
    )
    assert started.status_code == 200, started.text
    state = parse_qs(urlparse(started.json()["authorize_url"]).query)["state"][0]
    return client.get(
        "/shopify/callback",
        params=signed({"code": "abc", "shop": shop, "state": state, "timestamp": "1"}),
        follow_redirects=False,
    )
