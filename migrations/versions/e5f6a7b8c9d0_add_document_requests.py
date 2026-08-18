"""add document requests

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-17 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_request",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("subcontractor_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("document_type", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'COMPLETED', 'EXPIRED', 'CANCELLED')",
            name="ck_document_request_status",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["subcontractor_id"], ["subcontractor.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_document_request_active_lookup",
        "document_request",
        [
            "organization_id",
            "project_id",
            "subcontractor_id",
            "document_type",
            "status",
        ],
    )
    op.create_index(
        "ix_document_request_created_by_user_id",
        "document_request",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_document_request_document_id",
        "document_request",
        ["document_id"],
    )
    op.create_index(
        "ix_document_request_document_type",
        "document_request",
        ["document_type"],
    )
    op.create_index(
        "ix_document_request_organization_id",
        "document_request",
        ["organization_id"],
    )
    op.create_index(
        "ix_document_request_project_id",
        "document_request",
        ["project_id"],
    )
    op.create_index(
        "ix_document_request_status",
        "document_request",
        ["status"],
    )
    op.create_index(
        "ix_document_request_subcontractor_id",
        "document_request",
        ["subcontractor_id"],
    )
    op.create_index(
        "ix_document_request_token_hash",
        "document_request",
        ["token_hash"],
        unique=True,
    )


def downgrade():
    op.drop_index(
        "ix_document_request_token_hash",
        table_name="document_request",
    )
    op.drop_index(
        "ix_document_request_subcontractor_id",
        table_name="document_request",
    )
    op.drop_index(
        "ix_document_request_status",
        table_name="document_request",
    )
    op.drop_index(
        "ix_document_request_project_id",
        table_name="document_request",
    )
    op.drop_index(
        "ix_document_request_organization_id",
        table_name="document_request",
    )
    op.drop_index(
        "ix_document_request_document_type",
        table_name="document_request",
    )
    op.drop_index(
        "ix_document_request_document_id",
        table_name="document_request",
    )
    op.drop_index(
        "ix_document_request_created_by_user_id",
        table_name="document_request",
    )
    op.drop_index(
        "ix_document_request_active_lookup",
        table_name="document_request",
    )
    op.drop_table("document_request")
