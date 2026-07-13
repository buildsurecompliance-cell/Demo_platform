from datetime import date, datetime

from app.extensions import db


class Subcontractor(db.Model):

    __tablename__ = "subcontractor"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False,
        index=True
    )

    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organization.id"),
        nullable=False,
        index=True,
    )

    # ==========================
    # BASIC INFO
    # ==========================

    name = db.Column(
        db.String(150),
        nullable=False
    )

    email = db.Column(
        db.String(150),
        index=True
    )

    phone = db.Column(
        db.String(30)
    )

    role = db.Column(
        db.String(100)
    )

    timezone = db.Column(
        db.String(50),
        default="US/Eastern"
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    # ==========================
    # COMPLIANCE
    # ==========================

    coi_expiration = db.Column(
        db.Date,
        index=True
    )

    last_reminder_sent = db.Column(
        db.DateTime
    )

    # ==========================
    # RELATIONSHIPS
    # ==========================

    documents = db.relationship(
        "Document",
        backref="sub",
        lazy="selectin",
        cascade="all, delete-orphan"
    )

    organization = db.relationship(
        "Organization",
        back_populates="subcontractors",
    )

    projects = db.relationship(
        "ProjectSubcontractor",
        back_populates="subcontractor",
        lazy="joined",
        cascade="all, delete-orphan"
    )

    # ==========================
    # HELPERS
    # ==========================

    @property
    def linked_projects(self):
        return [
            link.project
            for link in self.projects
        ]

    @property
    def days_left(self):

        if not self.coi_expiration:
            return None

        return (
            self.coi_expiration - date.today()
        ).days

    @property
    def computed_status(self):

        days = self.days_left

        if days is None:
            return "compliant"

        if days < 0:
            return "expired"

        if days <= 30:
            return "at_risk"

        return "compliant"

    def __repr__(self):

        return (
            f"<Subcontractor "
            f"{self.id} "
            f"{self.name}>"
        )
