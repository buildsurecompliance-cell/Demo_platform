"""add stripe sync metadata

Revision ID: a1b2c3d4e5f6
Revises: 5e6f7a8b9c01
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "5e6f7a8b9c01"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("subscription") as batch_op:
        batch_op.add_column(
            sa.Column(
                "stripe_event_created_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "stripe_event_id",
                sa.String(length=255),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "stripe_last_synced_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "stripe_sync_error",
                sa.String(length=500),
                nullable=True,
            )
        )
        batch_op.create_index(
            "ix_subscription_stripe_event_created_at",
            ["stripe_event_created_at"],
        )
        batch_op.create_index(
            "ix_subscription_stripe_event_id",
            ["stripe_event_id"],
        )

    with op.batch_alter_table("billing_event") as batch_op:
        batch_op.add_column(
            sa.Column(
                "attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "last_attempt_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )


def downgrade():
    with op.batch_alter_table("billing_event") as batch_op:
        batch_op.drop_column("last_attempt_at")
        batch_op.drop_column("attempt_count")

    with op.batch_alter_table("subscription") as batch_op:
        batch_op.drop_index("ix_subscription_stripe_event_id")
        batch_op.drop_index("ix_subscription_stripe_event_created_at")
        batch_op.drop_column("stripe_sync_error")
        batch_op.drop_column("stripe_last_synced_at")
        batch_op.drop_column("stripe_event_id")
        batch_op.drop_column("stripe_event_created_at")
