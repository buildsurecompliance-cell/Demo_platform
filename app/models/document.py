from datetime import datetime

from app.extensions import db


class Document(db.Model):

    __tablename__ = "document"

    id = db.Column(db.Integer, primary_key=True)

    filename = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255))
    document_type = db.Column(db.String(100), index=True)
    version = db.Column(db.Integer, default=1)

    sub_id = db.Column(
        db.Integer,
        db.ForeignKey("subcontractor.id"),
        index=True
    )

    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project.id"),
        index=True
    )

    uploaded_by = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        index=True
    )

    uploader = db.relationship(
        "User",
        back_populates="uploaded_documents"
    )

    uploaded_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        index=True
    )

    ai_status = db.Column(
        db.String(50),
        default="not_analyzed",
        index=True
    )

    ai_confidence = db.Column(
        db.Float
    )

    ai_extracted_data = db.Column(
        db.JSON
    )

    ai_compliance_result = db.Column(
        db.JSON
    )

    ai_error = db.Column(
        db.Text
    )

    ai_analyzed_at = db.Column(
        db.DateTime
    )

    @property
    def display_name(self):
        return self.original_name or self.filename

    def __repr__(self):
        return (
            f"<Document "
            f"{self.id} "
            f"{self.document_type} "
            f"v{self.version}>"
        )