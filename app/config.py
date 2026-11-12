"""Settings, from the environment only."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: Literal["development", "test", "production"] = "development"
    # The app's name as merchants see it in Shopify, shown on its pages too.
    app_name: str = "Halyoon Connect"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/connector_dev"
    # Public origin of this service; Shopify's redirect and webhooks point here.
    public_base_url: str = "http://localhost:8100"

    # Signs the OAuth state. Never the encryption key below.
    secret_key: str = "dev-insecure-secret-change-me"
    # Fernet key for store tokens, tenant webhook secrets and Cloudflare tokens.
    encryption_key: str | None = None

    # The one Shopify app every tenant shares.
    shopify_api_key: str | None = None
    shopify_api_secret: str | None = None
    shopify_api_version: str = "2025-07"
    # Themes and files: the store profile (logo, social links) written into the
    # live theme. Theme writes also need Shopify's per-app exemption.
    # The app is shared with Yalla Pay (inventory, locations, orders, product
    # listings); markets and shipping are for the store setup. Must match the
    # app version's scopes in the Dev Dashboard (shopify.app.toml).
    shopify_scopes: str = (
        "read_products,write_products,write_publications,read_publications,"
        "read_themes,write_themes,write_files,read_markets,write_markets,"
        "read_shipping,write_shipping,read_inventory,read_locations,write_orders,"
        "write_draft_orders,read_fulfillments,unauthenticated_read_product_listings"
    )

    # JSON list of websites to create or update at startup (app/bootstrap.py).
    # Remove once applied: it carries tenant keys in plaintext.
    bootstrap_tenants: str | None = None

    # Shown on /support and /privacy; neither page names an address without it.
    support_email: str | None = None

    # The operator page (/operator) is mounted only when this is set.
    operator_password: str | None = None

    # Error reports; nothing is sent without a DSN (app/observability.py).
    sentry_dsn: str | None = None
    sentry_release: str | None = None

    worker_poll_seconds: int = 15
    http_timeout_seconds: float = 30.0

    @property
    def shopify_enabled(self) -> bool:
        return bool(self.shopify_api_key and self.shopify_api_secret and self.encryption_key)

    def enforce_production_safety(self) -> None:
        if self.app_env != "production":
            return
        problems: list[str] = []
        if self.secret_key == "dev-insecure-secret-change-me" or len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be a strong random value")
        if not self.encryption_key:
            problems.append("ENCRYPTION_KEY is required")
        elif self.encryption_key == self.secret_key:
            problems.append("ENCRYPTION_KEY must differ from SECRET_KEY")
        if not (self.shopify_api_key and self.shopify_api_secret):
            problems.append("SHOPIFY_API_KEY and SHOPIFY_API_SECRET are required")
        if self.operator_password is not None and len(self.operator_password) < 16:
            problems.append("OPERATOR_PASSWORD must be at least 16 characters")
        if problems:
            raise RuntimeError("Unsafe production configuration:\n  - " + "\n  - ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
