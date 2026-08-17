from datetime import datetime, timezone

from flask import current_app, has_app_context

from app.extensions import db
from app.models.billing_event import (
    EVENT_FAILED,
    EVENT_IGNORED,
    EVENT_PROCESSED,
    EVENT_PROCESSING,
    EVENT_RECEIVED,
    VALID_BILLING_EVENT_STATUSES,
    BillingEvent,
)
from app.services.subscription_service import (
    SubscriptionValidationError,
    validate_billing_provider,
)


INVALID_BILLING_EVENT_ID = "INVALID_BILLING_EVENT_ID"
INVALID_BILLING_EVENT_TYPE = "INVALID_BILLING_EVENT_TYPE"
INVALID_BILLING_EVENT_STATUS = "INVALID_BILLING_EVENT_STATUS"
EVENT_PROCESSING_RECENT = "EVENT_PROCESSING_RECENT"
EVENT_PROCESSING_EXPIRED = "EVENT_PROCESSING_EXPIRED"

MAX_ERROR_MESSAGE_LENGTH = 500
DEFAULT_PROCESSING_TIMEOUT_SECONDS = 300


class BillingEventValidationError(ValueError):

    def __init__(self, reason_code, message):
        self.reason_code = reason_code
        self.message = message
        super().__init__(message)


def get_billing_event(provider, external_event_id):
    provider = _validate_provider(provider)
    external_event_id = _validate_external_event_id(external_event_id)

    return (
        BillingEvent.query
        .filter_by(
            provider=provider,
            external_event_id=external_event_id,
        )
        .first()
    )


def create_billing_event(
    *,
    provider,
    external_event_id,
    event_type,
    organization=None,
    subscription=None,
):
    provider = _validate_provider(provider)
    external_event_id = _validate_external_event_id(external_event_id)
    event_type = _validate_event_type(event_type)

    event = BillingEvent(
        provider=provider,
        external_event_id=external_event_id,
        event_type=event_type,
        status=EVENT_RECEIVED,
        organization=organization,
        subscription=subscription,
    )
    db.session.add(event)
    db.session.flush()
    return event


def mark_event_processing(event, *, attempted_at=None):
    event = _set_event_status(event, EVENT_PROCESSING)
    attempted_at = _as_utc(attempted_at or _utc_now())
    event.attempt_count = (event.attempt_count or 0) + 1
    event.last_attempt_at = attempted_at
    return event


def mark_event_processed(event, *, processed_at=None):
    event = _set_event_status(event, EVENT_PROCESSED)
    event.processed_at = _as_utc(processed_at or _utc_now())
    event.error_message = None
    return event


def mark_event_failed(event, *, error_message, processed_at=None):
    event = _set_event_status(event, EVENT_FAILED)
    event.processed_at = _as_utc(processed_at or _utc_now())
    event.error_message = _sanitize_error_message(error_message)
    return event


def processing_timeout_seconds():
    if has_app_context():
        return int(
            current_app.config.get(
                "BILLING_EVENT_PROCESSING_TIMEOUT_SECONDS",
                DEFAULT_PROCESSING_TIMEOUT_SECONDS,
            )
        )

    return DEFAULT_PROCESSING_TIMEOUT_SECONDS


def processing_has_expired(event, *, now=None):
    if not event or event.status != EVENT_PROCESSING:
        return False

    attempted_at = _as_utc(event.last_attempt_at or event.processed_at)
    if attempted_at is None:
        return True

    now = _as_utc(now or _utc_now())
    age_seconds = (now - attempted_at).total_seconds()
    return age_seconds >= processing_timeout_seconds()


def mark_event_ignored(event, *, processed_at=None, reason_code=None):
    event = _set_event_status(event, EVENT_IGNORED)
    event.processed_at = _as_utc(processed_at or _utc_now())
    event.error_message = _sanitize_error_message(reason_code) if reason_code else None
    return event


def is_event_processed(provider, external_event_id):
    event = get_billing_event(provider, external_event_id)

    return bool(
        event
        and event.status == EVENT_PROCESSED
    )


def _set_event_status(event, status):
    if not event:
        raise BillingEventValidationError(
            INVALID_BILLING_EVENT_ID,
            "Billing event is required.",
        )

    if status not in VALID_BILLING_EVENT_STATUSES:
        raise BillingEventValidationError(
            INVALID_BILLING_EVENT_STATUS,
            "Invalid billing event status.",
        )

    event.status = status
    return event


def _validate_provider(provider):
    try:
        return validate_billing_provider(provider)
    except SubscriptionValidationError as exc:
        raise BillingEventValidationError(
            exc.reason_code,
            exc.message,
        ) from exc


def _validate_external_event_id(external_event_id):
    normalized = (external_event_id or "").strip()

    if not normalized or len(normalized) > 255:
        raise BillingEventValidationError(
            INVALID_BILLING_EVENT_ID,
            "External billing event ID is required.",
        )

    return normalized


def _validate_event_type(event_type):
    normalized = (event_type or "").strip()

    if not normalized or len(normalized) > 255:
        raise BillingEventValidationError(
            INVALID_BILLING_EVENT_TYPE,
            "Billing event type is required.",
        )

    return normalized


def _sanitize_error_message(error_message):
    message = (error_message or "").strip()

    if not message:
        return "Billing event failed."

    return message[:MAX_ERROR_MESSAGE_LENGTH]


def _utc_now():
    return datetime.now(timezone.utc)


def _as_utc(value):
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)
