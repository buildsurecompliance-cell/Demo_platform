"""add project required coverage

Revision ID: 9d1e2f3a4b5c
Revises: 8b7c6d5e4f30
Create Date: 2026-07-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "9d1e2f3a4b5c"
down_revision = "8b7c6d5e4f30"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project") as batch_op:
        batch_op.add_column(
            sa.Column(
                "required_coverage",
                sa.Integer(),
                nullable=True,
            )
        )


def downgrade():
    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_column("required_coverage")
