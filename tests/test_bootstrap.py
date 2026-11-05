from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import bootstrap
from app.config import Settings
from app.crypto import decrypt
from app.models import Tenant
from tests.conftest import auth

KEY = "sc_live_" + "k" * 43


def test_a_tenant_is_created_then_updated_from_the_environment(
    client: TestClient, db: Session, settings: Settings
) -> None:
    entry = {"slug": "site", "name": "Site", "origins": ["https://site.test/"],
             "webhook_url": "https://site.test/hooks", "subdomain_base": "site.test",
             "api_key": KEY, "webhook_secret": "whsec_s", "cloudflare_zone_id": "z",
             "cloudflare_token": "cf"}
    assert bootstrap.apply(db, settings, json.dumps([entry])) == ["site"]
    tenant = db.scalars(select(Tenant)).one()
    assert tenant.origins == ["https://site.test"]
    assert KEY not in tenant.api_key_hash
    assert decrypt(settings, tenant.webhook_secret_enc) == "whsec_s"
    assert decrypt(settings, tenant.cloudflare_token_enc or "") == "cf"
    assert client.get("/v1/tenant", headers=auth(KEY)).json()["subdomains_available"] is True

    bootstrap.apply(db, settings, json.dumps([{"slug": "site", "signup_url": "https://s"}]))
    db.expire_all()
    tenant = db.scalars(select(Tenant)).one()
    assert tenant.signup_url == "https://s" and tenant.cloudflare_zone_id == "z"
    assert db.scalars(select(Tenant)).all() == [tenant]


def test_a_new_tenant_without_a_proper_key_is_refused(db: Session, settings: Settings) -> None:
    with pytest.raises(ValueError, match="sc_live_"):
        bootstrap.apply(db, settings, json.dumps([{"slug": "x", "api_key": "short",
                                                   "webhook_secret": "w"}]))
    with pytest.raises(ValueError, match="key and secret"):
        bootstrap.apply(db, settings, json.dumps([{"slug": "x"}]))


def test_a_tenant_can_share_another_tenants_cloudflare_zone(
    db: Session, settings: Settings
) -> None:
    first = {"slug": "site", "api_key": KEY, "webhook_secret": "w",
             "cloudflare_zone_id": "z", "cloudflare_token": "cf"}
    second = {"slug": "staging", "api_key": "sc_live_" + "s" * 43, "webhook_secret": "w2",
              "cloudflare_from": "site"}
    bootstrap.apply(db, settings, json.dumps([first, second]))
    staging = db.scalars(select(Tenant).where(Tenant.slug == "staging")).one()
    assert staging.cloudflare_zone_id == "z"
    assert decrypt(settings, staging.cloudflare_token_enc or "") == "cf"
    with pytest.raises(ValueError, match="no tenant"):
        bootstrap.apply(db, settings, json.dumps([{"slug": "staging", "cloudflare_from": "nope"}]))
