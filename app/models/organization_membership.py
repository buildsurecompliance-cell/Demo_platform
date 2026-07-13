from datetime import datetime

from sqlalchemy.orm import validates

from app.extensions import db


ROLE_OWNER = "OWNER"
ROLE_ADMIN = "ADMIN"
ROLE_MEMBER = "MEMBER"

ORGANIZATION_ROLES = (
    ROLE_OWNER,
    ROLE_ADMIN,
    ROLE_MEMBER,
)


class OrganizationMembership(db.Model):

    __tablename__ = "organization_membership"

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

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False,
        index=True,
    )

    role = db.Column(
        db.String(20),
        nullable=False,
        default=ROLE_MEMBER,
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    organization = db.relationship(
        "Organization",
        back_populates="memberships",
    )

    user = db.relationship(
        "User",
        back_populates="organization_memberships",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "organization_id",
            "user_id",
            name="unique_organization_user_membership",
        ),
    )

    @validates("role")
    def validate_role(self, key, role):
        role = (role or "").strip().upper()

        if role not in ORGANIZATION_ROLES:
            raise ValueError("Invalid organization role.")

        return role

    def __repr__(self):
        return (
            f"<OrganizationMembership "
            f"org={self.organization_id} "
            f"user={self.user_id} "
            f"role={self.role}>"
        )
