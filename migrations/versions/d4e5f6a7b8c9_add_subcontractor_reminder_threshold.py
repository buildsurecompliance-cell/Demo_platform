"""add subcontractor reminder threshold

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-17 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("subcontractor") as batch_op:
        batch_op.add_column(
            sa.Column(
                "last_reminder_threshold",
                sa.Integer(),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "last_reminder_expiration",
                sa.Date(),
                nullable=True,
            )
        )


def downgrade():
    with op.batch_alter_table("subcontractor") as batch_op:
        batch_op.drop_column("last_reminder_expiration")
        batch_op.drop_column("last_reminder_threshold")
