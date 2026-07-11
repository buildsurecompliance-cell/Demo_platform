from datetime import datetime

from app.extensions import db


class ProjectSubcontractor(db.Model):

    __tablename__ = "project_subcontractor"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project.id"),
        nullable=False,
        index=True
    )

    subcontractor_id = db.Column(
        db.Integer,
        db.ForeignKey("subcontractor.id"),
        nullable=False,
        index=True
    )

    approved_for_project = db.Column(
        db.Boolean,
        default=False
    )

    coverage_limit = db.Column(
        db.Float,
        default=1000000
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    # ==========================
    # RELATIONSHIPS
    # ==========================

    project = db.relationship(
        "Project",
        back_populates="subs"
    )

    subcontractor = db.relationship(
        "Subcontractor",
        back_populates="projects"
    )

    @property
    def readiness(self):
        from app.services.readiness_service import calculate_readiness

        return calculate_readiness(self)

    @property
    def readiness_status(self):
        return self.readiness["status"]

    # ==========================
    # CONSTRAINTS
    # ==========================

    __table_args__ = (
        db.UniqueConstraint(
            "project_id",
            "subcontractor_id",
            name="unique_project_sub"
        ),
    )

    # ==========================
    # DEBUG
    # ==========================

    def __repr__(self):

        return (
            f"<ProjectSubcontractor "
            f"Project={self.project_id} "
            f"Sub={self.subcontractor_id}>"
        )
