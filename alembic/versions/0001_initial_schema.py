"""initial schema

Revision ID: 0001
Revises: none
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("tenants",
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('slug', sa.String(length=60), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('api_key_prefix', sa.String(length=16), nullable=False),
    sa.Column('api_key_hash', sa.String(length=64), nullable=False),
    sa.Column('webhook_url', sa.String(length=500), nullable=True),
    sa.Column('webhook_secret_enc', sa.Text(), nullable=False),
    sa.Column('return_origins', sa.Text(), nullable=False),
    sa.Column('subdomain_base', sa.String(length=255), nullable=True),
    sa.Column('cloudflare_zone_id', sa.String(length=64), nullable=True),
    sa.Column('cloudflare_token_enc', sa.Text(), nullable=True),
    sa.Column('signup_url', sa.String(length=500), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tenants')),
    sa.UniqueConstraint('api_key_hash', name=op.f('uq_tenants_api_key_hash')),
    sa.UniqueConstraint('slug', name=op.f('uq_tenants_slug'))
    )
    op.create_table('stores',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('external_ref', sa.String(length=120), nullable=False),
    sa.Column('shop_domain', sa.String(length=255), nullable=True),
    sa.Column('access_token_enc', sa.Text(), nullable=True),
    sa.Column('scopes', sa.String(length=500), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'CONNECTED', 'UNINSTALLED', name='store_status'), nullable=False),
    sa.Column('primary_domain', sa.String(length=255), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=True),
    sa.Column('subdomain', sa.String(length=63), nullable=True),
    sa.Column('cf_record_id', sa.String(length=64), nullable=True),
    sa.Column('connected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_stores_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stores')),
    sa.UniqueConstraint('shop_domain', name=op.f('uq_stores_shop_domain')),
    sa.UniqueConstraint('tenant_id', 'external_ref', name='uq_stores_tenant_ref')
    )
    op.create_index(op.f('ix_stores_tenant_id'), 'stores', ['tenant_id'], unique=False)
    op.create_table('webhook_deliveries',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('event', sa.String(length=60), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('gave_up_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_webhook_deliveries_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_webhook_deliveries'))
    )
    op.create_index('ix_webhook_deliveries_pending', 'webhook_deliveries', ['delivered_at', 'next_attempt_at'], unique=False)
    op.create_index(op.f('ix_webhook_deliveries_tenant_id'), 'webhook_deliveries', ['tenant_id'], unique=False)
    op.create_table('jobs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('store_id', sa.UUID(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('done_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_jobs_store_id_stores'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs'))
    )
    op.create_index('ix_jobs_pending', 'jobs', ['done_at', 'next_attempt_at'], unique=False)
    op.create_index(op.f('ix_jobs_store_id'), 'jobs', ['store_id'], unique=False)
    op.create_table('products',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('store_id', sa.UUID(), nullable=False),
    sa.Column('external_ref', sa.String(length=120), nullable=False),
    sa.Column('desired', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('shopify_product_id', sa.String(length=80), nullable=True),
    sa.Column('shopify_variant_id', sa.String(length=80), nullable=True),
    sa.Column('handle', sa.String(length=255), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('state', sa.Enum('QUEUED', 'SYNCED', 'SKIPPED', 'FAILED', 'DELETED', name='sync_state'), nullable=False),
    sa.Column('synced_hash', sa.String(length=64), nullable=True),
    sa.Column('synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_products_store_id_stores'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_products')),
    sa.UniqueConstraint('store_id', 'external_ref', name='uq_products_store_ref')
    )
    op.create_index(op.f('ix_products_store_id'), 'products', ['store_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_products_store_id"), table_name='products')
    op.drop_table('products')
    op.drop_index(op.f('ix_jobs_store_id'), table_name='jobs')
    op.drop_index('ix_jobs_pending', table_name='jobs')
    op.drop_table('jobs')
    op.drop_index(op.f('ix_webhook_deliveries_tenant_id'), table_name='webhook_deliveries')
    op.drop_index('ix_webhook_deliveries_pending', table_name='webhook_deliveries')
    op.drop_table('webhook_deliveries')
    op.drop_index(op.f('ix_stores_tenant_id'), table_name='stores')
    op.drop_table('stores')
    op.drop_table('tenants')
    sa.Enum(name='sync_state').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='store_status').drop(op.get_bind(), checkfirst=True)
