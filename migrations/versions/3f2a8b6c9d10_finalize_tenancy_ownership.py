"""finalize tenancy ownership

Revision ID: 3f2a8b6c9d10
Revises: 7c2b8d91f0a4
Create Date: 2026-07-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "3f2a8b6c9d10"
down_revision = "7c2b8d91f0a4"
branch_labels = None
depends_on = None


def _membership_organization_for_user(bind, table_name, row_id, user_id):
    rows = bind.execute(
        sa.text(
            """
            SELECT organization_id
            FROM organization_membership
            WHERE user_id = :user_id
            ORDER BY id
            """
        ),
        {"user_id": user_id},
    ).fetchall()

    organization_ids = {
        row.organization_id
        for row in rows
    }

    if len(organization_ids) != 1:
        raise RuntimeError(
            "Cannot infer a single organization for "
            f"{table_name} id={row_id} legacy user_id={user_id}."
        )

    return organization_ids.pop()


def _build_backfill_plan(bind, table_name):
    rows = bind.execute(
        sa.text(
            f"""
            SELECT id, user_id
            FROM {table_name}
            WHERE organization_id IS NULL
            ORDER BY id
            """
        )
    ).fetchall()

    plan = []

    for row in rows:
        if row.user_id is None:
            raise RuntimeError(
                f"Cannot infer organization for {table_name} id={row.id}."
            )

        organization_id = _membership_organization_for_user(
            bind,
            table_name,
            row.id,
            row.user_id,
        )

        plan.append(
            {
                "organization_id": organization_id,
                "id": row.id,
            }
        )

    return plan


def _apply_backfill_plan(bind, table_name, plan):
    for item in plan:
        bind.execute(
            sa.text(
                f"""
                UPDATE {table_name}
                SET organization_id = :organization_id
                WHERE id = :id
                """
            ),
            item,
        )


def upgrade():
    bind = op.get_bind()

    project_plan = _build_backfill_plan(bind, "project")
    subcontractor_plan = _build_backfill_plan(bind, "subcontractor")

    _apply_backfill_plan(bind, "project", project_plan)
    _apply_backfill_plan(bind, "subcontractor", subcontractor_plan)

    with op.batch_alter_table("project") as batch_op:
        batch_op.alter_column(
            "organization_id",
            existing_type=sa.Integer(),
            nullable=False,
        )

    with op.batch_alter_table("subcontractor") as batch_op:
        batch_op.alter_column(
            "organization_id",
            existing_type=sa.Integer(),
            nullable=False,
        )


def downgrade():
    with op.batch_alter_table("subcontractor") as batch_op:
        batch_op.alter_column(
            "organization_id",
            existing_type=sa.Integer(),
            nullable=True,
        )

    with op.batch_alter_table("project") as batch_op:
        batch_op.alter_column(
            "organization_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
