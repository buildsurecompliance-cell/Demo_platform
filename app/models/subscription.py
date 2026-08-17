from datetime import datetime, timezone

from app.extensions import db


def _utc_now():
    return datetime.now(timezone.utc)


class Subscription(db.Model):

    __tablename__ = "subscription"
    __table_args__ = (
        db.CheckConstraint(
            (
                "provider IN ('internal', 'stripe')"
            ),
            name="ck_subscription_provider",
        ),
        db.CheckConstraint(
            (
                "status IN ("
                "'incomplete', "
                "'incomplete_expired', "
                "'trialing', "
                "'active', "
                "'past_due', "
                "'unpaid', "
                "'paused', "
                "'canceled', "
                "'inactive'"
                ")"
            ),
            name="ck_subscription_status",
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
        unique=True,
        index=True,
    )

    provider = db.Column(
        db.String(50),
        nullable=False,
        default="internal",
    )

    status = db.Column(
        db.String(50),
        nullable=False,
        default="inactive",
        index=True,
    )

    billing_customer_id = db.Column(
        db.String(255),
        nullable=True,
        unique=True,
    )

    billing_subscription_id = db.Column(
        db.String(255),
        nullable=True,
        unique=True,
    )

    billing_price_id = db.Column(
        db.String(255),
        nullable=True,
    )

    current_period_start = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    current_period_end = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    trial_start = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    trial_end = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    cancel_at_period_end = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    cancel_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    canceled_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    ended_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    stripe_event_created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    stripe_event_id = db.Column(
        db.String(255),
        nullable=True,
        index=True,
    )

    stripe_last_synced_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    stripe_sync_error = db.Column(
        db.String(500),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=_utc_now,
        nullable=False,
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
    )

    organization = db.relationship(
        "Organization",
        back_populates="subscription",
    )

    billing_events = db.relationship(
        "BillingEvent",
        back_populates="subscription",
        lazy=True,
    )

    def __repr__(self):
        return (
            f"<Subscription {self.id} "
            f"organization_id={self.organization_id} "
            f"status={self.status}>"
        )
