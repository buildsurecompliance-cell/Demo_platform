"""add user last active organization

Revision ID: 8b7c6d5e4f30
Revises: 4a9f1c2d3e5b
Create Date: 2026-07-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "8b7c6d5e4f30"
down_revision = "4a9f1c2d3e5b"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(
            sa.Column(
                "last_active_organization_id",
                sa.Integer(),
                nullable=True,
            )
        )
        batch_op.create_foreign_key(
            "fk_user_last_active_organization_id_organization",
            "organization",
            ["last_active_organization_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_user_last_active_organization_id",
            ["last_active_organization_id"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_index("ix_user_last_active_organization_id")
        batch_op.drop_constraint(
            "fk_user_last_active_organization_id_organization",
            type_="foreignkey",
        )
        batch_op.drop_column("last_active_organization_id")
