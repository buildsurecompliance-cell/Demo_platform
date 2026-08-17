"""add organization subscription

Revision ID: 2b4c6d8e0f12
Revises: 9d1e2f3a4b5c
Create Date: 2026-07-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "2b4c6d8e0f12"
down_revision = "9d1e2f3a4b5c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "subscription",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("billing_customer_id", sa.String(length=255), nullable=True),
        sa.Column("billing_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("billing_price_id", sa.String(length=255), nullable=True),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "provider IN ('internal', 'stripe')",
            name="ck_subscription_provider",
        ),
        sa.CheckConstraint(
            (
                "status IN ("
                "'incomplete', "
                "'incomplete_expired', "
                "'trialing', "
                "'active', "
                "'past_due', "
                "'unpaid', "
                "'paused', "
                "'canceled', "
                "'inactive'"
                ")"
            ),
            name="ck_subscription_status",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            name="uq_subscription_organization_id",
        ),
        sa.UniqueConstraint(
            "billing_customer_id",
            name="uq_subscription_billing_customer_id",
        ),
        sa.UniqueConstraint(
            "billing_subscription_id",
            name="uq_subscription_billing_subscription_id",
        ),
    )

    with op.batch_alter_table("subscription") as batch_op:
        batch_op.create_index(
            batch_op.f("ix_subscription_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_subscription_status"),
            ["status"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_subscription_current_period_end"),
            ["current_period_end"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_subscription_trial_end"),
            ["trial_end"],
            unique=False,
        )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO subscription (
                organization_id,
                provider,
                status,
                cancel_at_period_end,
                created_at,
                updated_at
            )
            SELECT
                organization.id,
                'internal',
                'active',
                0,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM organization
            WHERE NOT EXISTS (
                SELECT 1
                FROM subscription
                WHERE subscription.organization_id = organization.id
            )
            """
        )
    )

    with op.batch_alter_table("subscription") as batch_op:
        batch_op.alter_column(
            "cancel_at_period_end",
            existing_type=sa.Boolean(),
            server_default=None,
            nullable=False,
        )


def downgrade():
    op.drop_table("subscription")
