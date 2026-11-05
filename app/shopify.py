"""Calls to Shopify, and the checks on calls Shopify makes to us."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

import httpx

SHOP_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")
MAX_RETRIES = 4


class ShopifyError(Exception):
    """Shopify refused or failed a call; the message is safe to show a tenant."""


def normalize_shop(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"^https?://", "", value).split("/", 1)[0]
    if value.startswith("admin.shopify.com"):
        return ""
    if "." not in value:
        value = f"{value}.myshopify.com"
    return value


def is_shop_domain(value: str) -> bool:
    return bool(SHOP_DOMAIN.fullmatch(value))


def authorize_url(shop: str, *, api_key: str, scopes: str, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {"client_id": api_key, "scope": scopes, "redirect_uri": redirect_uri, "state": state}
    )
    return f"https://{shop}/admin/oauth/authorize?{query}"


def verify_query_hmac(params: Mapping[str, str], secret: str) -> bool:
    received = params.get("hmac", "")
    message = "&".join(
        f"{k}={v}" for k, v in sorted(params.items()) if k not in ("hmac", "signature")
    )
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return bool(received) and hmac.compare_digest(digest, received)


def verify_webhook_hmac(body: bytes, header: str | None, secret: str) -> bool:
    if not header:
        return False
    digest = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(digest, header)


def exchange_code(
    http: httpx.Client, shop: str, *, api_key: str, api_secret: str, code: str
) -> tuple[str, str]:
    response = http.post(
        f"https://{shop}/admin/oauth/access_token",
        json={"client_id": api_key, "client_secret": api_secret, "code": code},
    )
    if response.status_code != 200:
        raise ShopifyError(f"token exchange failed ({response.status_code})")
    data = response.json()
    return str(data["access_token"]), str(data.get("scope", ""))


def revoke_token(http: httpx.Client, shop: str, token: str) -> None:
    http.delete(
        f"https://{shop}/admin/api_permissions/current.json",
        headers={"X-Shopify-Access-Token": token},
    )


class ShopifyClient:
    def __init__(self, http: httpx.Client, shop: str, token: str, api_version: str) -> None:
        self._http = http
        self._url = f"https://{shop}/admin/api/{api_version}/graphql.json"
        self._token = token

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(MAX_RETRIES + 1):
            response = self._http.post(
                self._url,
                json={"query": query, "variables": variables or {}},
                headers={"X-Shopify-Access-Token": self._token},
            )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < MAX_RETRIES:
                    time.sleep(float(response.headers.get("Retry-After", 2**attempt)))
                    continue
                raise ShopifyError(f"Shopify unavailable ({response.status_code})")
            if response.status_code == 401:
                raise ShopifyError("the store's access token was refused")
            if response.status_code != 200:
                raise ShopifyError(f"Shopify answered {response.status_code}")
            body: dict[str, Any] = response.json()
            errors = body.get("errors")
            if errors:
                throttled = any(
                    (e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors
                )
                if throttled and attempt < MAX_RETRIES:
                    time.sleep(2**attempt)
                    continue
                raise ShopifyError("; ".join(str(e.get("message")) for e in errors))
            return dict(body.get("data") or {})
        raise ShopifyError("Shopify kept throttling")


def raise_user_errors(result: Mapping[str, Any], ignore: tuple[str, ...] = ()) -> None:
    errors = [
        e
        for e in result.get("userErrors") or []
        if not any(word in str(e.get("message", "")).lower() for word in ignore)
    ]
    if errors:
        raise ShopifyError("; ".join(str(e.get("message")) for e in errors))
