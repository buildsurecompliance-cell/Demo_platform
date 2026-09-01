"""add organization tenancy

Revision ID: 7c2b8d91f0a4
Revises: ebe17429fa03
Create Date: 2026-07-12 23:15:00.000000

"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "7c2b8d91f0a4"
down_revision = "ebe17429fa03"
branch_labels = None
depends_on = None


def _default_org_name(email):
    prefix = (email or "User").split("@", 1)[0].strip()

    if not prefix:
        prefix = "User"

    return f"{prefix}'s Organization"


def _user_backfill_select():
    user_table = sa.table(
        "user",
        sa.column("id", sa.Integer),
        sa.column("email", sa.String),
    )

    return (
        sa.select(
            user_table.c.id,
            user_table.c.email,
        )
        .order_by(user_table.c.id)
    )


def upgrade():
    organization_table = sa.table(
        "organization",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )

    op.create_table(
        "organization",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "organization_membership",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            name="unique_organization_user_membership",
        ),
    )

    with op.batch_alter_table("organization_membership") as batch_op:
        batch_op.create_index(
            batch_op.f("ix_organization_membership_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_organization_membership_user_id"),
            ["user_id"],
            unique=False,
        )

    op.create_table(
        "organization_invitation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=120), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("invited_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["invited_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    with op.batch_alter_table("organization_invitation") as batch_op:
        batch_op.create_index(
            batch_op.f("ix_organization_invitation_email"),
            ["email"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_organization_invitation_invited_by"),
            ["invited_by"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_organization_invitation_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_organization_invitation_token_hash"),
            ["token_hash"],
            unique=True,
        )
        batch_op.create_index(
            "unique_pending_organization_invitation",
            ["organization_id", "email"],
            unique=True,
            sqlite_where=sa.text("accepted_at IS NULL"),
            postgresql_where=sa.text("accepted_at IS NULL"),
        )

    with op.batch_alter_table("project") as batch_op:
        batch_op.add_column(
            sa.Column("organization_id", sa.Integer(), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_project_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_project_organization_id_organization",
            "organization",
            ["organization_id"],
            ["id"],
        )

    with op.batch_alter_table("subcontractor") as batch_op:
        batch_op.add_column(
            sa.Column("organization_id", sa.Integer(), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_subcontractor_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_subcontractor_organization_id_organization",
            "organization",
            ["organization_id"],
            ["id"],
        )

    bind = op.get_bind()
    now = datetime.utcnow()

    users = bind.execute(_user_backfill_select()).fetchall()

    for user in users:
        result = bind.execute(
            sa.insert(organization_table)
            .values(
                name=_default_org_name(user.email),
                created_at=now,
                updated_at=now,
            )
            .returning(organization_table.c.id)
        )
        organization_id = result.scalar_one()

        bind.execute(
            sa.text(
                """
                INSERT INTO organization_membership
                    (organization_id, user_id, role, created_at)
                VALUES
                    (:organization_id, :user_id, 'OWNER', :created_at)
                """
            ),
            {
                "organization_id": organization_id,
                "user_id": user.id,
                "created_at": now,
            },
        )

        bind.execute(
            sa.text(
                """
                UPDATE project
                SET organization_id = :organization_id
                WHERE user_id = :user_id
                """
            ),
            {
                "organization_id": organization_id,
                "user_id": user.id,
            },
        )

        bind.execute(
            sa.text(
                """
                UPDATE subcontractor
                SET organization_id = :organization_id
                WHERE user_id = :user_id
                """
            ),
            {
                "organization_id": organization_id,
                "user_id": user.id,
            },
        )


def downgrade():
    with op.batch_alter_table("subcontractor") as batch_op:
        batch_op.drop_constraint(
            "fk_subcontractor_organization_id_organization",
            type_="foreignkey",
        )
        batch_op.drop_index(
            batch_op.f("ix_subcontractor_organization_id")
        )
        batch_op.drop_column("organization_id")

    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_constraint(
            "fk_project_organization_id_organization",
            type_="foreignkey",
        )
        batch_op.drop_index(
            batch_op.f("ix_project_organization_id")
        )
        batch_op.drop_column("organization_id")

    with op.batch_alter_table("organization_invitation") as batch_op:
        batch_op.drop_index(
            "unique_pending_organization_invitation"
        )
        batch_op.drop_index(
            batch_op.f("ix_organization_invitation_token_hash")
        )
        batch_op.drop_index(
            batch_op.f("ix_organization_invitation_organization_id")
        )
        batch_op.drop_index(
            batch_op.f("ix_organization_invitation_invited_by")
        )
        batch_op.drop_index(
            batch_op.f("ix_organization_invitation_email")
        )

    op.drop_table("organization_invitation")

    with op.batch_alter_table("organization_membership") as batch_op:
        batch_op.drop_index(
            batch_op.f("ix_organization_membership_user_id")
        )
        batch_op.drop_index(
            batch_op.f("ix_organization_membership_organization_id")
        )

    op.drop_table("organization_membership")
    op.drop_table("organization")
