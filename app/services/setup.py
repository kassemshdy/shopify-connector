"""Store setup: a market for the website's country and a delivery rate there.

A website asks (``PUT /v1/stores/{ref}/setup``) with the rate it wants shown
-- name, price, a line such as "2-3 business days" -- and a job does the rest,
**adding only what is missing**:

1. which scopes the store has *actually* granted are read from Shopify
   (``currentAppInstallation``), because a merchant approving a new app
   version inside Shopify never passes through our OAuth callback;
2. the country is put in an active market priced in the given currency,
   unless a market already includes it (that market is left as it is);
3. the default delivery profile gets the rate for that country: in the zone
   that already covers the country if there is one (a method with the same
   name is updated, never duplicated), otherwise in a new zone.

Nothing the merchant set up is deleted or renamed. What was done is kept on
the store as ``setup_report`` and sent to the website as ``store.updated``.
The store currency cannot be changed by an app; the report says when it
differs.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.models import Store
from app.shopify import ShopifyClient, raise_user_errors

logger = logging.getLogger(__name__)

SETUP = "store.setup"
NEEDED_SCOPES = frozenset({"write_markets", "write_shipping"})

INSTALLATION = "{ currentAppInstallation { accessScopes { handle } } shop { currencyCode } }"

MARKETS = """
query Markets {
  markets(first: 50) {
    nodes {
      id name status
      currencySettings { baseCurrency { currencyCode } }
      conditions { regionsCondition { regions(first: 250) {
        nodes { ... on MarketRegionCountry { code } }
      } } }
    }
  }
}
"""

MARKET_CREATE = """
mutation MarketCreate($input: MarketCreateInput!) {
  marketCreate(input: $input) {
    market { id name status }
    userErrors { field message code }
  }
}
"""

PROFILES = """
query Profiles {
  deliveryProfiles(first: 20) {
    nodes {
      id default
      profileLocationGroups {
        locationGroup { id }
        locationGroupZones(first: 50) {
          nodes {
            zone { id name countries { code { countryCode } } }
            methodDefinitions(first: 50) {
              nodes {
                id name
                rateProvider { ... on DeliveryRateDefinition { id } }
              }
            }
          }
        }
      }
    }
  }
}
"""

PROFILE_UPDATE = """
mutation ProfileUpdate($id: ID!, $profile: DeliveryProfileInput!) {
  deliveryProfileUpdate(id: $id, profile: $profile) {
    profile { id }
    userErrors { field message }
  }
}
"""


def granted_scopes(client: ShopifyClient) -> tuple[set[str], str | None]:
    data = client.graphql(INSTALLATION)
    handles = {
        str(s.get("handle"))
        for s in ((data.get("currentAppInstallation") or {}).get("accessScopes") or [])
    }
    return handles, (data.get("shop") or {}).get("currencyCode")


def _nodes(conn: Any) -> list[dict[str, Any]]:
    return list((conn or {}).get("nodes") or [])


def _countries(market: dict[str, Any]) -> set[str]:
    regions = ((market.get("conditions") or {}).get("regionsCondition") or {}).get("regions")
    return {str(n.get("code")) for n in _nodes(regions) if n.get("code")}


def ensure_market(client: ShopifyClient, country: str, currency: str) -> dict[str, Any]:
    markets = _nodes(client.graphql(MARKETS).get("markets"))
    having = [m for m in markets if country in _countries(m)]
    active = [m for m in having if str(m.get("status")) == "ACTIVE"]
    if active or having:
        market = (active or having)[0]
        base = ((market.get("currencySettings") or {}).get("baseCurrency") or {})
        return {
            "action": "existing",
            "name": market.get("name"),
            "active": bool(active),
            "currency": base.get("currencyCode"),
        }
    result = client.graphql(MARKET_CREATE, {"input": {
        "name": country_name(country),
        "enabled": True,
        "conditions": {"regionsCondition": {"regions": [{"countryCode": country}]}},
        "currencySettings": {"baseCurrency": currency, "localCurrencies": False},
    }}).get("marketCreate") or {}
    raise_user_errors(result)
    return {"action": "created", "name": country_name(country), "active": True,
            "currency": currency}


def _method(rate: dict[str, Any], currency: str) -> dict[str, Any]:
    return {
        "name": rate["name"],
        "description": rate.get("description") or None,
        "active": True,
        "rateDefinition": {"price": {"amount": str(rate["price"]), "currencyCode": currency}},
    }


def ensure_rate(
    client: ShopifyClient, country: str, currency: str, rate: dict[str, Any]
) -> dict[str, Any]:
    profiles = _nodes(client.graphql(PROFILES).get("deliveryProfiles"))
    if not profiles:
        return {"action": "no_profile"}
    profile = next((p for p in profiles if p.get("default")), profiles[0])
    groups = profile.get("profileLocationGroups") or []
    if not groups:
        return {"action": "no_location"}

    for group in groups:
        for gz in _nodes(group.get("locationGroupZones")):
            zone = gz.get("zone") or {}
            codes = {
                str((c.get("code") or {}).get("countryCode")) for c in zone.get("countries") or []
            }
            if country not in codes:
                continue
            same = next(
                (m for m in _nodes(gz.get("methodDefinitions"))
                 if str(m.get("name", "")).strip().lower() == rate["name"].strip().lower()),
                None,
            )
            method = _method(rate, currency)
            if same is not None:
                method["id"] = same["id"]
                rate_id = (same.get("rateProvider") or {}).get("id")
                if rate_id:
                    method["rateDefinition"]["id"] = rate_id
                zone_input = {"id": zone["id"], "methodDefinitionsToUpdate": [method]}
                action = "updated"
            else:
                zone_input = {"id": zone["id"], "methodDefinitionsToCreate": [method]}
                action = "added"
            _update(client, profile["id"], group["locationGroup"]["id"],
                    {"zonesToUpdate": [zone_input]})
            return {"action": action, "zone": zone.get("name"), "name": rate["name"]}

    zone_input = {
        "name": country_name(country),
        "countries": [{"code": country, "includeAllProvinces": True}],
        "methodDefinitionsToCreate": [_method(rate, currency)],
    }
    _update(client, profile["id"], groups[0]["locationGroup"]["id"],
            {"zonesToCreate": [zone_input]})
    return {"action": "created", "zone": country_name(country), "name": rate["name"]}


def _update(client: ShopifyClient, profile_id: str, group_id: str, change: dict[str, Any]) -> None:
    result = client.graphql(PROFILE_UPDATE, {
        "id": profile_id,
        "profile": {"locationGroupsToUpdate": [{"id": group_id, **change}]},
    }).get("deliveryProfileUpdate") or {}
    raise_user_errors(result)


COUNTRY_NAMES = {"LB": "Lebanon"}


def country_name(code: str) -> str:
    return COUNTRY_NAMES.get(code, code)


def run_setup(store: Store, client: ShopifyClient, payload: dict[str, Any]) -> dict[str, Any]:
    """Do the setup and return the report also kept on the store."""
    country = str(payload.get("country") or "LB").upper()
    currency = str(payload.get("currency") or "USD").upper()
    rate = {
        "name": str(payload["rate"]["name"]),
        "price": Decimal(str(payload["rate"]["price"])),
        "description": payload["rate"].get("description"),
    }
    report: dict[str, Any] = {"at": datetime.now(UTC).isoformat(), "country": country}

    scopes, store_currency = granted_scopes(client)
    store.scopes = ",".join(sorted(scopes))
    report["store_currency"] = store_currency
    report["currency_ok"] = store_currency == currency
    implied = scopes | {s.replace("write_", "read_") for s in scopes if s.startswith("write_")}
    missing = sorted(NEEDED_SCOPES - implied)
    if missing:
        report["missing_scopes"] = missing
        report["ok"] = False
        logger.info("Store setup needs scopes", extra={"store_id": str(store.id)})
        return report

    report["market"] = ensure_market(client, country, currency)
    report["rate"] = ensure_rate(client, country, currency, rate)
    report["ok"] = bool(report["currency_ok"]) and bool(report["market"].get("active"))
    logger.info("Store setup done", extra={"store_id": str(store.id)})
    return report
