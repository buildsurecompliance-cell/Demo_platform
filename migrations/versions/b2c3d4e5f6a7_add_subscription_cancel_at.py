"""add subscription cancel_at

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-11 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("subscription", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("cancel_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_subscription_cancel_at"),
            ["cancel_at"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("subscription", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_subscription_cancel_at"))
        batch_op.drop_column("cancel_at")
