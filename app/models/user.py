from datetime import datetime

from flask_login import UserMixin

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

from app.extensions import db, login_manager


class User(UserMixin, db.Model):

    __tablename__ = "user"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    email = db.Column(
        db.String(120),
        unique=True,
        index=True,
        nullable=False
    )

    password_hash = db.Column(
        db.String(200),
        nullable=False
    )

    # SaaS Subscription
    paid = db.Column(
        db.Boolean,
        default=False
    )

    # User Timezone
    timezone = db.Column(
        db.String(50),
        default="US/Eastern"
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    # ==========================
    # RELATIONSHIPS
    # ==========================

    projects = db.relationship(
        "Project",
        backref="owner",
        lazy=True,
    )

    subs = db.relationship(
        "Subcontractor",
        backref="owner",
        lazy=True,
    )

    organization_memberships = db.relationship(
        "OrganizationMembership",
        back_populates="user",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    uploaded_documents = db.relationship(
    "Document",
    back_populates="uploader",
    lazy=True
    )

    # ==========================
    # PASSWORD
    # ==========================

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(
            self.password_hash,
            password
        )


@login_manager.user_loader
def load_user(user_id):

    if not user_id:
        return None

    try:
        return db.session.get(
            User,
            int(user_id)
        )

    except (ValueError, TypeError):
        return None
