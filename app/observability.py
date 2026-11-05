"""Error reports to Sentry, with nothing a merchant or a website signs in with.

Inert without ``SENTRY_DSN``. Personal data collection is off, request bodies
are never attached, and the query string is dropped from every event: it is
where Shopify puts an OAuth code, an HMAC and our signed state. Headers that
carry a key (a tenant's bearer token, Shopify's signature) are removed too.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings

DROPPED_HEADERS = frozenset(
    {"authorization", "cookie", "x-shopify-hmac-sha256", "x-shopify-access-token",
     "x-connector-signature"}
)


def scrub(event: dict[str, Any], _hint: Any = None) -> dict[str, Any]:
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("query_string", None)
        request.pop("data", None)
        request.pop("cookies", None)
        if isinstance(request.get("url"), str):
            request["url"] = request["url"].split("?", 1)[0]
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                k: v for k, v in headers.items() if k.lower() not in DROPPED_HEADERS
            }
    return event


def init_sentry(settings: Settings) -> bool:
    if not settings.sentry_dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        release=settings.sentry_release,
        send_default_pii=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        before_send=scrub,  # type: ignore[arg-type]
    )
    return True
