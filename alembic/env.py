from __future__ import annotations

from sqlalchemy import engine_from_config, pool

from alembic import context
from app import models  # noqa: F401  (registers the tables)
from app.config import get_settings
from app.db import Base

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run()
