from datetime import datetime

from app.extensions import db


class Organization(db.Model):

    __tablename__ = "organization"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(255),
        nullable=False
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

    def __repr__(self):
        return f"<Organization {self.id} {self.name}>"
