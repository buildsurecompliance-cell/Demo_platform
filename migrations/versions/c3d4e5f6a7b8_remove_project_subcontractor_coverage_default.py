"""remove project subcontractor coverage default

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-17 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project_subcontractor", schema=None) as batch_op:
        batch_op.alter_column(
            "coverage_limit",
            existing_type=sa.Float(),
            nullable=True,
            server_default=None,
        )


def downgrade():
    with op.batch_alter_table("project_subcontractor", schema=None) as batch_op:
        batch_op.alter_column(
            "coverage_limit",
            existing_type=sa.Float(),
            nullable=True,
            server_default=sa.text("1000000"),
        )
