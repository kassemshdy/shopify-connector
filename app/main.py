"""The connector app, and the worker that drains jobs and webhook deliveries."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import bootstrap
from app.api import home, shopify_routes, v1
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.errors import AppError
from app.observability import init_sentry
from app.services import events, sync

logger = logging.getLogger(__name__)


async def run_worker(settings: Settings) -> None:
    with httpx.Client(timeout=settings.http_timeout_seconds) as http:
        while True:
            await asyncio.sleep(settings.worker_poll_seconds)
            for step in (sync.drain_once, events.deliver_once):
                try:
                    await asyncio.to_thread(step, SessionLocal, settings, http)
                except Exception:
                    logger.exception("Worker step failed")


def create_app(settings: Settings | None = None, *, worker: bool = True) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)
    # httpx logs every URL it calls; keep it quiet so no request detail lands
    # in the deployment logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    init_sentry(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        settings.enforce_production_safety()
        if settings.bootstrap_tenants:
            with SessionLocal() as db:
                bootstrap.apply(db, settings, settings.bootstrap_tenants)
        task = asyncio.create_task(run_worker(settings)) if worker else None
        yield
        if task is not None:
            task.cancel()

    app = FastAPI(title="Shopify Connector", version="1.0.0", lifespan=lifespan)

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status_code
        )

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(home.router)
    app.include_router(v1.router)
    app.include_router(shopify_routes.router)
    return app


app = create_app()
