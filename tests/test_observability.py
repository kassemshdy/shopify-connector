from __future__ import annotations

from app.observability import scrub


def test_no_code_signature_key_or_body_reaches_sentry() -> None:
    event = {"request": {
        "url": "https://connector.test/shopify/callback?code=abc&hmac=def&state=ghi",
        "query_string": "code=abc&hmac=def&state=ghi",
        "data": {"api_key": "sc_live_x"},
        "cookies": {"s": "1"},
        "headers": {"Authorization": "Bearer sc_live_x", "X-Shopify-Hmac-Sha256": "h",
                    "X-Connector-Signature": "t=1,v1=x", "User-Agent": "Shopify"},
    }}
    out = str(scrub(event))
    for secret in ("abc", "def", "ghi", "sc_live_x", "t=1,v1=x", "'s'"):
        assert secret not in out
    assert event["request"]["url"] == "https://connector.test/shopify/callback"
    assert event["request"]["headers"] == {"User-Agent": "Shopify"}
