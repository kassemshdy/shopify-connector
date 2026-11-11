"""stores.page_url: the shop's own page on the website

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stores", sa.Column("page_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    # Drops nothing: removing the column would lose data. Undo with a new migration.
    pass
