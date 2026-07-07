from datetime import datetime

from app.extensions import db


class Document(db.Model):

    __tablename__ = "document"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    # ==========================
    # FILE INFO
    # ==========================

    filename = db.Column(
        db.String(255),
        nullable=False
    )

    original_name = db.Column(
        db.String(255)
    )

    document_type = db.Column(
        db.String(100),
        index=True
    )

    version = db.Column(
        db.Integer,
        default=1
    )

    # ==========================
    # RELATIONSHIPS
    # ==========================

    # Linked Subcontractor
    sub_id = db.Column(
        db.Integer,
        db.ForeignKey("subcontractor.id"),
        index=True
    )

    # Linked Project
    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project.id"),
        index=True
    )

    # Uploaded By
    uploaded_by = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        index=True
    )

    uploader = db.relationship(
    "User",
    back_populates="uploaded_documents"
    )     

    # ==========================
    # TIMESTAMPS
    # ==========================

    uploaded_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        index=True
    )

    # ==========================
    # HELPERS
    # ==========================

    @property
    def display_name(self):
        return self.original_name or self.filename

    # ==========================
    # DEBUG
    # ==========================

    def __repr__(self):

        return (
            f"<Document "
            f"{self.id} "
            f"{self.document_type} "
            f"v{self.version}>"
        )