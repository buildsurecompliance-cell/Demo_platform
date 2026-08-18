from datetime import datetime

from app.extensions import db


DOCUMENT_REQUEST_PENDING = "PENDING"
DOCUMENT_REQUEST_COMPLETED = "COMPLETED"
DOCUMENT_REQUEST_EXPIRED = "EXPIRED"
DOCUMENT_REQUEST_CANCELLED = "CANCELLED"

DOCUMENT_REQUEST_STATUSES = (
    DOCUMENT_REQUEST_PENDING,
    DOCUMENT_REQUEST_COMPLETED,
    DOCUMENT_REQUEST_EXPIRED,
    DOCUMENT_REQUEST_CANCELLED,
)


class DocumentRequest(db.Model):

    __tablename__ = "document_request"
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('PENDING', 'COMPLETED', 'EXPIRED', 'CANCELLED')",
            name="ck_document_request_status",
        ),
        db.Index(
            "ix_document_request_active_lookup",
            "organization_id",
            "project_id",
            "subcontractor_id",
            "document_type",
            "status",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organization.id"),
        nullable=False,
        index=True,
    )

    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project.id"),
        nullable=False,
        index=True,
    )

    subcontractor_id = db.Column(
        db.Integer,
        db.ForeignKey("subcontractor.id"),
        nullable=False,
        index=True,
    )

    document_id = db.Column(
        db.Integer,
        db.ForeignKey("document.id"),
        index=True,
    )

    document_type = db.Column(
        db.String(100),
        nullable=False,
        index=True,
    )

    token_hash = db.Column(
        db.String(128),
        nullable=False,
        unique=True,
        index=True,
    )

    status = db.Column(
        db.String(20),
        nullable=False,
        default=DOCUMENT_REQUEST_PENDING,
        server_default=DOCUMENT_REQUEST_PENDING,
        index=True,
    )

    expires_at = db.Column(
        db.DateTime,
        nullable=False,
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    sent_at = db.Column(
        db.DateTime,
    )

    last_sent_at = db.Column(
        db.DateTime,
    )

    completed_at = db.Column(
        db.DateTime,
    )

    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False,
        index=True,
    )

    organization = db.relationship(
        "Organization",
        back_populates="document_requests",
    )

    project = db.relationship("Project")

    subcontractor = db.relationship("Subcontractor")

    document = db.relationship("Document")

    created_by = db.relationship(
        "User",
        foreign_keys=[created_by_user_id],
    )

    def __repr__(self):
        return (
            f"<DocumentRequest {self.id} "
            f"{self.document_type} {self.status}>"
        )
