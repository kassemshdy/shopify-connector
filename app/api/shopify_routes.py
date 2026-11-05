"""Routes Shopify calls. Authenticated by Shopify's HMAC, never a tenant key."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse

from app.api.deps import DbSession, Http, ShopifySettings
from app.errors import AppError
from app.services.stores import StoreService, with_status
from app.shopify import verify_webhook_hmac

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/shopify", include_in_schema=False)


@router.get("/callback")
def callback(request: Request, db: DbSession, settings: ShopifySettings, http: Http) -> Response:
    service = StoreService(db, settings, http)
    params = dict(request.query_params)
    try:
        return RedirectResponse(service.complete_connect(params))
    except AppError as exc:
        logger.warning("Shopify callback refused: %s", exc.code)
        try:
            return RedirectResponse(with_status(service.read_state(params.get("state", ""))["ret"],
                                                exc.code))
        except AppError:
            return Response("The connection could not be confirmed.", status_code=400)


@router.post("/webhooks")
async def webhooks(
    request: Request, db: DbSession, settings: ShopifySettings, http: Http
) -> Response:
    body = await request.body()
    assert settings.shopify_api_secret
    if not verify_webhook_hmac(
        body, request.headers.get("X-Shopify-Hmac-Sha256"), settings.shopify_api_secret
    ):
        return Response(status_code=401)
    await run_in_threadpool(
        StoreService(db, settings, http).handle_shopify_webhook,
        request.headers.get("X-Shopify-Topic", ""),
        request.headers.get("X-Shopify-Shop-Domain", ""),
    )
    return Response(status_code=200)
