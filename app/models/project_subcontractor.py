from datetime import datetime

from sqlalchemy import event, select

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

    # Legacy/manual field retained for compatibility and audit. Readiness must
    # use validated COI evidence for actual insurance coverage.
    coverage_limit = db.Column(
        db.Float,
        nullable=True,
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


def _validate_same_organization(mapper, connection, target):
    from app.models.project import Project
    from app.models.subcontractor import Subcontractor

    if not target.project_id or not target.subcontractor_id:
        return

    project_org_id = connection.execute(
        select(Project.organization_id)
        .where(Project.id == target.project_id)
    ).scalar_one_or_none()

    subcontractor_org_id = connection.execute(
        select(Subcontractor.organization_id)
        .where(Subcontractor.id == target.subcontractor_id)
    ).scalar_one_or_none()

    if project_org_id is None or subcontractor_org_id is None:
        raise ValueError(
            "Project and subcontractor must both belong to an organization."
        )

    if project_org_id != subcontractor_org_id:
        raise ValueError(
            "Project and subcontractor must belong to the same organization."
        )


event.listen(
    ProjectSubcontractor,
    "before_insert",
    _validate_same_organization,
)
event.listen(
    ProjectSubcontractor,
    "before_update",
    _validate_same_organization,
)
