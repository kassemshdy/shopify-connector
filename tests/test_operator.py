from __future__ import annotations

import re
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import operator as operator_module
from app.api.deps import get_http
from app.config import Settings, get_settings
from app.crypto import decrypt
from app.main import create_app
from app.models import Tenant
from tests.conftest import auth, make_tenant

PASSWORD = "operator-password-long-enough"


@pytest.fixture
def op_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"operator_password": PASSWORD})


def _client(settings: Settings, http: httpx.Client) -> TestClient:
    app = create_app(settings, worker=False)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_http] = lambda: http
    # https, so the Secure session cookie is sent back.
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def op(op_settings: Settings, http: httpx.Client) -> Iterator[TestClient]:
    operator_module._failures.clear()
    with _client(op_settings, http) as client:
        yield client


def sign_in(client: TestClient, password: str = PASSWORD) -> httpx.Response:
    return client.post("/operator/login", data={"password": password}, follow_redirects=False)


def csrf(page: httpx.Response) -> str:
    match = re.search(r'name="csrf" value="([0-9a-f]+)"', page.text)
    assert match, page.text[:500]
    return match.group(1)


def test_the_page_does_not_exist_without_a_password(client: TestClient) -> None:
    assert client.get("/operator").status_code == 404
    assert client.post("/operator/login", data={"password": "x"}).status_code == 404


def test_every_page_needs_a_session(op: TestClient, db: Session, op_settings: Settings) -> None:
    make_tenant(db, op_settings, "site")
    for path in ("/operator", "/operator/sites/new", "/operator/sites/site"):
        response = op.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/operator/login"
    assert op.post("/operator/sites/site/rotate-key", follow_redirects=False).status_code == 303


def test_a_wrong_password_is_refused_and_five_block_the_address(op: TestClient) -> None:
    for _ in range(5):
        assert "Wrong password" in sign_in(op, "nope").text
    assert "Too many attempts" in sign_in(op).text
    assert "op_session" not in op.cookies


def test_the_session_cookie_is_locked_down_and_pages_cannot_be_framed(op: TestClient) -> None:
    response = sign_in(op)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"].lower()
    for flag in ("httponly", "samesite=strict", "secure", "path=/operator"):
        assert flag in cookie
    page = op.get("/operator")
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"


def test_a_session_dies_when_the_password_changes(
    op: TestClient, op_settings: Settings, http: httpx.Client
) -> None:
    sign_in(op)
    changed = op_settings.model_copy(update={"operator_password": "another-password-entirely"})
    with _client(changed, http) as other:
        other.cookies.update(op.cookies)
        assert other.get("/operator", follow_redirects=False).status_code == 303


def test_adding_a_website_shows_its_key_once_and_the_key_works(
    op: TestClient, db: Session
) -> None:
    sign_in(op)
    form = op.get("/operator/sites/new")
    created = op.post("/operator/sites/new", data={
        "csrf": csrf(form), "slug": "souk", "name": "Souk",
        "origins": "https://souk.example\nhttps://www.souk.example/",
        "webhook_url": "https://souk.example/hooks", "subdomain_base": "souk.example",
    })
    key = re.search(r"<code>(sc_live_[^<]+)</code>", created.text)
    secret = re.search(r"<code>(whsec_[^<]+)</code>", created.text)
    assert key and secret
    tenant = db.scalars(select(Tenant).where(Tenant.slug == "souk")).one()
    assert tenant.origins == ["https://souk.example", "https://www.souk.example"]
    assert tenant.webhook_url == "https://souk.example/hooks"
    assert op.get("/v1/tenant", headers=auth(key.group(1))).json()["slug"] == "souk"
    again = op.get("/operator/sites/souk").text
    assert key.group(1) not in again and secret.group(1) not in again


def test_a_form_without_its_token_changes_nothing(op: TestClient, db: Session,
                                                  op_settings: Settings) -> None:
    _, key, _ = make_tenant(db, op_settings, "site")
    sign_in(op)
    assert op.post("/operator/sites/site/rotate-key", data={"csrf": "0" * 64}).status_code == 403
    assert op.post("/operator/sites/site/active", data={}).status_code == 403
    assert op.get("/v1/tenant", headers=auth(key)).status_code == 200


def test_replacing_a_key_stops_the_old_one(op: TestClient, db: Session,
                                           op_settings: Settings) -> None:
    _, key, _ = make_tenant(db, op_settings, "site")
    sign_in(op)
    page = op.post("/operator/sites/site/rotate-key",
                   data={"csrf": csrf(op.get("/operator/sites/site"))})
    new_key = re.search(r"<code>(sc_live_[^<]+)</code>", page.text)
    assert new_key
    assert op.get("/v1/tenant", headers=auth(key)).status_code == 401
    assert op.get("/v1/tenant", headers=auth(new_key.group(1))).status_code == 200


def test_the_cloudflare_token_is_stored_encrypted_and_never_shown(
    op: TestClient, db: Session, op_settings: Settings
) -> None:
    make_tenant(db, op_settings, "site")
    make_tenant(db, op_settings, "other")
    sign_in(op)
    token = csrf(op.get("/operator/sites/site"))
    op.post("/operator/sites/site/cloudflare",
            data={"csrf": token, "zone_id": "zone1", "token": "cf-secret-token"})
    page = op.get("/operator/sites/site").text
    assert "cf-secret-token" not in page and "A token is set" in page
    db.expire_all()
    site = db.scalars(select(Tenant).where(Tenant.slug == "site")).one()
    assert decrypt(op_settings, site.cloudflare_token_enc or "") == "cf-secret-token"

    # Saving without a token keeps it; a second site can share it.
    op.post("/operator/sites/site/cloudflare", data={"csrf": token, "zone_id": "zone1"})
    op.post("/operator/sites/other/cloudflare/copy", data={"csrf": token, "source": "site"})
    db.expire_all()
    other = db.scalars(select(Tenant).where(Tenant.slug == "other")).one()
    assert other.cloudflare_zone_id == "zone1"
    assert decrypt(op_settings, other.cloudflare_token_enc or "") == "cf-secret-token"


def test_a_disabled_website_is_locked_out(op: TestClient, db: Session,
                                          op_settings: Settings) -> None:
    _, key, _ = make_tenant(db, op_settings, "site")
    sign_in(op)
    op.post("/operator/sites/site/active", data={"csrf": csrf(op.get("/operator"))})
    assert op.get("/v1/tenant", headers=auth(key)).status_code == 401


def test_production_refuses_a_short_operator_password(settings: Settings) -> None:
    unsafe = settings.model_copy(update={"app_env": "production", "operator_password": "short"})
    with pytest.raises(RuntimeError, match="OPERATOR_PASSWORD"):
        unsafe.enforce_production_safety()
