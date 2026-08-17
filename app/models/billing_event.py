from datetime import datetime, timezone

from app.extensions import db


EVENT_RECEIVED = "received"
EVENT_PROCESSING = "processing"
EVENT_PROCESSED = "processed"
EVENT_FAILED = "failed"
EVENT_IGNORED = "ignored"

VALID_BILLING_EVENT_STATUSES = frozenset({
    EVENT_RECEIVED,
    EVENT_PROCESSING,
    EVENT_PROCESSED,
    EVENT_FAILED,
    EVENT_IGNORED,
})


def _utc_now():
    return datetime.now(timezone.utc)


class BillingEvent(db.Model):

    __tablename__ = "billing_event"
    __table_args__ = (
        db.UniqueConstraint(
            "provider",
            "external_event_id",
            name="uq_billing_event_provider_external_event_id",
        ),
        db.CheckConstraint(
            (
                "status IN ("
                "'received', "
                "'processing', "
                "'processed', "
                "'failed', "
                "'ignored'"
                ")"
            ),
            name="ck_billing_event_status",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    provider = db.Column(
        db.String(50),
        nullable=False,
        index=True,
    )

    external_event_id = db.Column(
        db.String(255),
        nullable=False,
        index=True,
    )

    event_type = db.Column(
        db.String(255),
        nullable=False,
        index=True,
    )

    status = db.Column(
        db.String(50),
        nullable=False,
        default=EVENT_RECEIVED,
        index=True,
    )

    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organization.id"),
        nullable=True,
        index=True,
    )

    subscription_id = db.Column(
        db.Integer,
        db.ForeignKey("subscription.id"),
        nullable=True,
        index=True,
    )

    processed_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    attempt_count = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    last_attempt_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    error_message = db.Column(
        db.Text,
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=_utc_now,
        nullable=False,
        index=True,
    )

    organization = db.relationship(
        "Organization",
        back_populates="billing_events",
    )

    subscription = db.relationship(
        "Subscription",
        back_populates="billing_events",
    )

    def __repr__(self):
        return (
            f"<BillingEvent {self.id} "
            f"provider={self.provider} "
            f"external_event_id={self.external_event_id}>"
        )
