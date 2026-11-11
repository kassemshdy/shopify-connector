"""The API websites call. Every route is scoped to the calling tenant."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Response, status
from sqlalchemy import func, select

from app.api.deps import CurrentTenant, DbSession, Http, ShopifySettings
from app.errors import Conflict
from app.models import Job, Product, Store, SyncState, Tenant
from app.schemas import (
    BulkIn,
    ConnectIn,
    ConnectOut,
    PageIn,
    ProductIn,
    ProductOut,
    ProfileIn,
    Queued,
    SetupIn,
    StoreOut,
    SubdomainIn,
    TenantOut,
)
from app.services import products as product_service
from app.services import profile as profile_service
from app.services import setup as setup_service
from app.services.stores import StoreService, check_page_url, get_store
from app.services.sync import enqueue

router = APIRouter(prefix="/v1", tags=["v1"])

Ref = Annotated[str, Path(min_length=1, max_length=120)]


def store_out(db: DbSession, tenant: Tenant, store: Store) -> StoreOut:
    synced = db.scalar(
        select(func.count()).select_from(Product)
        .where(Product.store_id == store.id, Product.state == SyncState.SYNCED)
    ) or 0
    pending = db.scalar(
        select(func.count()).select_from(Job)
        .where(Job.store_id == store.id, Job.done_at.is_(None))
    ) or 0
    hostname = (
        f"{store.subdomain}.{tenant.subdomain_base}"
        if store.subdomain and tenant.subdomain_base else None
    )
    return StoreOut(
        store_ref=store.external_ref,
        status=store.status.value,
        shop_domain=store.shop_domain,
        storefront_url=store.storefront_url,
        subdomain=store.subdomain,
        subdomain_hostname=hostname,
        subdomain_live=hostname is not None and store.primary_domain == hostname,
        currency=store.currency,
        connected_at=store.connected_at,
        last_sync_at=store.last_sync_at,
        last_error=store.last_error,
        products_synced=synced,
        setup=store.setup_report,
        jobs_pending=pending,
        signup_url=tenant.signup_url,
    )


def product_out(product: Product) -> ProductOut:
    return ProductOut(
        store_ref=product.store.external_ref,
        product_ref=product.external_ref,
        state=product.state.value,
        handle=product.handle,
        active=product.active,
        buy_url=product.buy_url,
        error=product.last_error,
        synced_at=product.synced_at,
    )


@router.get("/tenant", response_model=TenantOut)
def tenant_info(tenant: CurrentTenant) -> TenantOut:
    """Your own settings, needed before any store exists."""
    return TenantOut(
        slug=tenant.slug,
        name=tenant.name,
        signup_url=tenant.signup_url,
        subdomain_base=tenant.subdomain_base,
        subdomains_available=bool(
            tenant.subdomain_base and tenant.cloudflare_zone_id and tenant.cloudflare_token_enc
        ),
    )


@router.post("/stores/{store_ref}/connect", response_model=ConnectOut)
def connect(
    store_ref: Ref, payload: ConnectIn, tenant: CurrentTenant, db: DbSession,
    settings: ShopifySettings, http: Http,
) -> ConnectOut:
    url = StoreService(db, settings, http).start_connect(
        tenant, store_ref, payload.shop, str(payload.return_url),
        str(payload.page_url) if payload.page_url else None,
    )
    return ConnectOut(authorize_url=url)


@router.get("/stores/{store_ref}", response_model=StoreOut)
def get(store_ref: Ref, tenant: CurrentTenant, db: DbSession) -> StoreOut:
    return store_out(db, tenant, get_store(db, tenant, store_ref))


@router.delete(
    "/stores/{store_ref}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
def disconnect(
    store_ref: Ref, tenant: CurrentTenant, db: DbSession, settings: ShopifySettings, http: Http
) -> Response:
    StoreService(db, settings, http).disconnect(tenant, get_store(db, tenant, store_ref))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/stores/{store_ref}/setup", response_model=Queued, status_code=status.HTTP_202_ACCEPTED
)
def put_setup(store_ref: Ref, payload: SetupIn, tenant: CurrentTenant, db: DbSession) -> Queued:
    """Queue the store setup: the country in an active market, and a delivery rate there.

    Missing scopes are not refused here: the job reads what the store has really
    granted (an approval inside Shopify never reaches us) and reports them.
    """
    store = get_store(db, tenant, store_ref)
    if not store.is_live:
        raise Conflict("not_connected", "The store is not connected.")
    enqueue(db, store, setup_service.SETUP, payload.model_dump(mode="json"))
    db.commit()
    return Queued(queued=1)


@router.put("/stores/{store_ref}/page", response_model=StoreOut)
def put_page(store_ref: Ref, payload: PageIn, tenant: CurrentTenant, db: DbSession) -> StoreOut:
    """Record the shop's own page on the website, linked from the app page in Shopify."""
    store = get_store(db, tenant, store_ref)
    check_page_url(tenant, str(payload.page_url))
    store.page_url = str(payload.page_url)
    db.commit()
    return store_out(db, tenant, store)


@router.post("/stores/{store_ref}/subdomain", response_model=StoreOut)
def subdomain(
    store_ref: Ref, payload: SubdomainIn, tenant: CurrentTenant, db: DbSession,
    settings: ShopifySettings, http: Http,
) -> StoreOut:
    store = get_store(db, tenant, store_ref)
    StoreService(db, settings, http).assign_subdomain(tenant, store, payload.subdomain)
    return store_out(db, tenant, store)


@router.put(
    "/stores/{store_ref}/profile", response_model=Queued, status_code=status.HTTP_202_ACCEPTED
)
def put_profile(
    store_ref: Ref, payload: ProfileIn, tenant: CurrentTenant, db: DbSession,
    settings: ShopifySettings,
) -> Queued:
    """Queue the shop's logo and social links for its store's live theme."""
    store = get_store(db, tenant, store_ref)
    if not store.is_live:
        raise Conflict("not_connected", "The store is not connected.")
    missing = profile_service.missing_scopes(store)
    if missing:
        raise Conflict(
            "reconnect_required",
            "Connect the store again to grant theme access: " + ", ".join(sorted(missing)),
        )
    enqueue(db, store, profile_service.PROFILE, payload.model_dump(mode="json"))
    db.commit()
    return Queued(queued=1)


@router.put(
    "/stores/{store_ref}/products/{product_ref}",
    response_model=ProductOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def put_product(
    store_ref: Ref, product_ref: Ref, payload: ProductIn, tenant: CurrentTenant, db: DbSession
) -> ProductOut:
    store = get_store(db, tenant, store_ref)
    product = product_service.upsert(db, store, product_ref, payload.desired())
    db.commit()
    return product_out(product)


@router.post(
    "/stores/{store_ref}/products/bulk",
    response_model=Queued,
    status_code=status.HTTP_202_ACCEPTED,
)
def bulk(store_ref: Ref, payload: BulkIn, tenant: CurrentTenant, db: DbSession) -> Queued:
    store = get_store(db, tenant, store_ref)
    for item in payload.products:
        product_service.upsert(db, store, item.product_ref, item.desired())
    db.commit()
    return Queued(queued=len(payload.products))


@router.get("/stores/{store_ref}/products/{product_ref}", response_model=ProductOut)
def get_product(
    store_ref: Ref, product_ref: Ref, tenant: CurrentTenant, db: DbSession
) -> ProductOut:
    store = get_store(db, tenant, store_ref)
    return product_out(product_service.get_product(db, store, product_ref))


@router.delete(
    "/stores/{store_ref}/products/{product_ref}",
    response_model=Queued,
    status_code=status.HTTP_202_ACCEPTED,
)
def delete_product(
    store_ref: Ref, product_ref: Ref, tenant: CurrentTenant, db: DbSession
) -> Queued:
    store = get_store(db, tenant, store_ref)
    product_service.delete(db, store, product_ref)
    db.commit()
    return Queued(queued=1)
