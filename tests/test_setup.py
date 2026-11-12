from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import SessionLocal
from app.models import Job, Store
from app.services import setup, sync
from tests.conftest import FakeRemote, auth, connect, make_tenant

GRANTED = ["write_products", "write_publications", "write_markets", "write_shipping"]


class SetupRemote(FakeRemote):
    """FakeRemote, plus markets and delivery profiles."""

    def __init__(self) -> None:
        super().__init__()
        self.scopes = list(GRANTED)
        self.currency = "USD"
        self.markets: list[dict[str, Any]] = [{
            "id": "gid://shopify/Market/1", "name": "United States", "status": "ACTIVE",
            "currencySettings": {"baseCurrency": {"currencyCode": "USD"}},
            "conditions": {"regionsCondition": {"regions": {"nodes": [{"code": "US"}]}}},
        }]
        self.zones: list[dict[str, Any]] = [{
            "zone": {"id": "gid://shopify/DeliveryZone/1", "name": "Domestic",
                     "countries": [{"code": {"countryCode": "US"}}]},
            "methodDefinitions": {"nodes": [{"id": "gid://shopify/DeliveryMethodDefinition/1",
                                             "name": "Standard", "rateProvider": {"id": "r1"}}]},
        }]
        self.created_market: dict[str, Any] | None = None
        self.profile_updates: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("graphql.json"):
            body = json.loads(request.content)
            q, v = body["query"], body.get("variables") or {}
            if "currentAppInstallation" in q:
                return httpx.Response(200, json={"data": {
                    "currentAppInstallation": {
                        "accessScopes": [{"handle": s} for s in self.scopes]},
                    "shop": {"currencyCode": self.currency}}})
            if "MarketCreate" in q:
                self.created_market = v["input"]
                return httpx.Response(200, json={"data": {"marketCreate": {
                    "market": {"id": "gid://shopify/Market/2", "name": "Lebanon",
                               "status": "ACTIVE"}, "userErrors": []}}})
            if "query Markets" in q:
                return httpx.Response(200, json={"data": {"markets": {"nodes": self.markets}}})
            if "query Profiles" in q:
                return httpx.Response(200, json={"data": {"deliveryProfiles": {"nodes": [{
                    "id": "gid://shopify/DeliveryProfile/1", "default": True,
                    "profileLocationGroups": [{
                        "locationGroup": {"id": "gid://shopify/DeliveryLocationGroup/1"},
                        "locationGroupZones": {"nodes": self.zones}}]}]}}})
            if "ProfileUpdate" in q:
                self.profile_updates.append(v)
                return httpx.Response(200, json={"data": {"deliveryProfileUpdate": {
                    "profile": {"id": v["id"]}, "userErrors": []}}})
        return super().__call__(request)


@pytest.fixture
def remote() -> SetupRemote:
    return SetupRemote()


@pytest.fixture
def http(remote: SetupRemote) -> Iterator[httpx.Client]:
    with httpx.Client(transport=httpx.MockTransport(remote)) as client:
        yield client


RATE = {"rate": {"name": "Standard Delivery", "price": "4.00", "description": "2-3 business days"}}


def _run(client: TestClient, db: Session, settings: Settings, http: httpx.Client,
         body: dict[str, Any] = RATE) -> dict[str, Any]:
    _, key, _ = make_tenant(db, settings, "alpha")
    connect(client, key, "alpha")
    r = client.put("/v1/stores/shop-1/setup", headers=auth(key), json=body)
    assert r.status_code == 202, r.text
    sync.drain_once(SessionLocal, settings, http)
    store = db.scalars(select(Store)).one()
    db.refresh(store)
    assert store.setup_report is not None
    report: dict[str, Any] = store.setup_report
    out = client.get("/v1/stores/shop-1", headers=auth(key)).json()
    assert out["setup"] == report
    return report


def test_a_store_without_lebanon_gets_a_usd_market_and_a_new_zone(
    client: TestClient, db: Session, settings: Settings, http: httpx.Client, remote: SetupRemote
) -> None:
    report = _run(client, db, settings, http)
    assert report["ok"] is True
    assert report["market"]["action"] == "created"
    assert remote.created_market == {
        "name": "Lebanon", "enabled": True,
        "conditions": {"regionsCondition": {"regions": [{"countryCode": "LB"}]}},
        "currencySettings": {"baseCurrency": "USD", "localCurrencies": False},
    }
    change = remote.profile_updates[0]["profile"]["locationGroupsToUpdate"][0]
    zone = change["zonesToCreate"][0]
    assert zone["countries"] == [{"code": "LB", "includeAllProvinces": True}]
    method = zone["methodDefinitionsToCreate"][0]
    assert method["name"] == "Standard Delivery" and method["description"] == "2-3 business days"
    assert method["rateDefinition"]["price"] == {"amount": "4.00", "currencyCode": "USD"}


def test_a_market_and_zone_already_covering_lebanon_are_reused_never_duplicated(
    client: TestClient, db: Session, settings: Settings, http: httpx.Client, remote: SetupRemote
) -> None:
    remote.markets[0]["conditions"]["regionsCondition"]["regions"]["nodes"].append({"code": "LB"})
    remote.zones[0]["zone"]["countries"].append({"code": {"countryCode": "LB"}})
    remote.zones[0]["methodDefinitions"]["nodes"].append({
        "id": "gid://shopify/DeliveryMethodDefinition/9", "name": "standard delivery",
        "rateProvider": {"id": "gid://shopify/DeliveryRateDefinition/9"}})
    report = _run(client, db, settings, http)
    assert report["market"] == {"action": "existing", "name": "United States", "active": True,
                                "currency": "USD"}
    assert remote.created_market is None
    zone = remote.profile_updates[0]["profile"]["locationGroupsToUpdate"][0]["zonesToUpdate"][0]
    method = zone["methodDefinitionsToUpdate"][0]
    assert method["id"] == "gid://shopify/DeliveryMethodDefinition/9"
    assert method["rateDefinition"]["id"] == "gid://shopify/DeliveryRateDefinition/9"
    assert report["rate"]["action"] == "updated"


def test_missing_scopes_and_another_currency_are_reported_and_nothing_is_changed(
    client: TestClient, db: Session, settings: Settings, http: httpx.Client, remote: SetupRemote
) -> None:
    remote.scopes = ["write_products"]
    remote.currency = "LBP"
    report = _run(client, db, settings, http)
    assert report["ok"] is False
    assert report["missing_scopes"] == ["write_markets", "write_shipping"]
    assert report["currency_ok"] is False and report["store_currency"] == "LBP"
    assert remote.created_market is None and remote.profile_updates == []
    job = db.scalars(select(Job).where(Job.kind == setup.SETUP)).one()
    db.refresh(job)
    assert job.done_at is not None and job.last_error is None


def test_a_rate_needs_a_price_and_a_connected_store(
    client: TestClient, db: Session, settings: Settings
) -> None:
    _, key, _ = make_tenant(db, settings, "alpha")
    r = client.put("/v1/stores/shop-1/setup", headers=auth(key), json={"rate": {"name": "x"}})
    assert r.status_code == 422
