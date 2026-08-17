from datetime import datetime

from app.extensions import db


class Organization(db.Model):

    __tablename__ = "organization"
    __table_args__ = (
        db.CheckConstraint(
            "plan_key IN ('STARTER', 'PROFESSIONAL', 'ENTERPRISE')",
            name="ck_organization_plan_key",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(255),
        nullable=False
    )

    plan_key = db.Column(
        db.String(32),
        nullable=False,
        default="STARTER",
        server_default="STARTER",
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    memberships = db.relationship(
        "OrganizationMembership",
        back_populates="organization",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    projects = db.relationship(
        "Project",
        back_populates="organization",
        lazy=True,
    )

    subcontractors = db.relationship(
        "Subcontractor",
        back_populates="organization",
        lazy=True,
    )

    invitations = db.relationship(
        "OrganizationInvitation",
        back_populates="organization",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    subscription = db.relationship(
        "Subscription",
        back_populates="organization",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    billing_events = db.relationship(
        "BillingEvent",
        back_populates="organization",
        lazy=True,
    )

    def __repr__(self):
        return f"<Organization {self.id} {self.name}>"
