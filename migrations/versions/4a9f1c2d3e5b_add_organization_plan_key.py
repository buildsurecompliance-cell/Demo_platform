"""add organization plan key

Revision ID: 4a9f1c2d3e5b
Revises: 3f2a8b6c9d10
Create Date: 2026-07-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "4a9f1c2d3e5b"
down_revision = "3f2a8b6c9d10"
branch_labels = None
depends_on = None

PLAN_KEYS = (
    "STARTER",
    "PROFESSIONAL",
    "ENTERPRISE",
)


def upgrade():
    with op.batch_alter_table("organization") as batch_op:
        batch_op.add_column(
            sa.Column(
                "plan_key",
                sa.String(length=32),
                server_default="STARTER",
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            "ck_organization_plan_key",
            "plan_key IN ('STARTER', 'PROFESSIONAL', 'ENTERPRISE')",
        )

    with op.batch_alter_table("organization") as batch_op:
        batch_op.alter_column(
            "plan_key",
            existing_type=sa.String(length=32),
            server_default=None,
            nullable=False,
        )


def downgrade():
    with op.batch_alter_table("organization") as batch_op:
        batch_op.drop_constraint(
            "ck_organization_plan_key",
            type_="check",
        )
        batch_op.drop_column("plan_key")
