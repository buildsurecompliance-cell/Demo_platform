"""add billing event idempotency

Revision ID: 5e6f7a8b9c01
Revises: 2b4c6d8e0f12
Create Date: 2026-07-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "5e6f7a8b9c01"
down_revision = "2b4c6d8e0f12"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "billing_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default="received",
        ),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("subscription_id", sa.Integer(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            (
                "status IN ("
                "'received', "
                "'processing', "
                "'processed', "
                "'failed', "
                "'ignored'"
                ")"
            ),
            name="ck_billing_event_status",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscription.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "external_event_id",
            name="uq_billing_event_provider_external_event_id",
        ),
    )

    with op.batch_alter_table("billing_event") as batch_op:
        batch_op.create_index(
            batch_op.f("ix_billing_event_provider"),
            ["provider"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_billing_event_external_event_id"),
            ["external_event_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_billing_event_event_type"),
            ["event_type"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_billing_event_status"),
            ["status"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_billing_event_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_billing_event_subscription_id"),
            ["subscription_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_billing_event_created_at"),
            ["created_at"],
            unique=False,
        )

    with op.batch_alter_table("billing_event") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=50),
            server_default=None,
            nullable=False,
        )


def downgrade():
    op.drop_table("billing_event")
