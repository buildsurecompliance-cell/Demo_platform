from datetime import datetime

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.organization_membership import ORGANIZATION_ROLES


class OrganizationInvitation(db.Model):

    __tablename__ = "organization_invitation"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organization.id"),
        nullable=False,
        index=True,
    )

    email = db.Column(
        db.String(120),
        nullable=False,
        index=True,
    )

    role = db.Column(
        db.String(20),
        nullable=False,
    )

    token_hash = db.Column(
        db.String(128),
        nullable=False,
        unique=True,
        index=True,
    )

    expires_at = db.Column(
        db.DateTime,
        nullable=False,
    )

    accepted_at = db.Column(
        db.DateTime,
    )

    invited_by = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False,
        index=True,
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    organization = db.relationship(
        "Organization",
        back_populates="invitations",
    )

    inviter = db.relationship(
        "User",
        foreign_keys=[invited_by],
    )

    __table_args__ = (
        db.Index(
            "unique_pending_organization_invitation",
            "organization_id",
            "email",
            unique=True,
            sqlite_where=db.text("accepted_at IS NULL"),
            postgresql_where=db.text("accepted_at IS NULL"),
        ),
    )

    @validates("email")
    def validate_email(self, key, email):
        email = (email or "").strip().lower()

        if not email:
            raise ValueError("Invitation email is required.")

        return email

    @validates("role")
    def validate_role(self, key, role):
        role = (role or "").strip().upper()

        if role not in ORGANIZATION_ROLES:
            raise ValueError("Invalid invitation role.")

        return role

    def __repr__(self):
        return (
            f"<OrganizationInvitation "
            f"org={self.organization_id} "
            f"email={self.email}>"
        )
