import logging

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from flask import current_app, has_app_context

from app.extensions import db
from app.models.subscription import Subscription


PROVIDER_INTERNAL = "internal"
PROVIDER_STRIPE = "stripe"

VALID_BILLING_PROVIDERS = frozenset({
    PROVIDER_INTERNAL,
    PROVIDER_STRIPE,
})

STATUS_INCOMPLETE = "incomplete"
STATUS_INCOMPLETE_EXPIRED = "incomplete_expired"
STATUS_TRIALING = "trialing"
STATUS_ACTIVE = "active"
STATUS_PAST_DUE = "past_due"
STATUS_UNPAID = "unpaid"
STATUS_PAUSED = "paused"
STATUS_CANCELED = "canceled"
STATUS_INACTIVE = "inactive"

VALID_SUBSCRIPTION_STATUSES = frozenset({
    STATUS_INCOMPLETE,
    STATUS_INCOMPLETE_EXPIRED,
    STATUS_TRIALING,
    STATUS_ACTIVE,
    STATUS_PAST_DUE,
    STATUS_UNPAID,
    STATUS_PAUSED,
    STATUS_CANCELED,
    STATUS_INACTIVE,
})

ACCESS_ACTIVE = "ACCESS_ACTIVE"
ACCESS_TRIALING = "ACCESS_TRIALING"
ACCESS_PAST_DUE_GRACE_PERIOD = "ACCESS_PAST_DUE_GRACE_PERIOD"

NO_SUBSCRIPTION = "NO_SUBSCRIPTION"
SUBSCRIPTION_INACTIVE = "SUBSCRIPTION_INACTIVE"
SUBSCRIPTION_INCOMPLETE = "SUBSCRIPTION_INCOMPLETE"
SUBSCRIPTION_INCOMPLETE_EXPIRED = "SUBSCRIPTION_INCOMPLETE_EXPIRED"
SUBSCRIPTION_UNPAID = "SUBSCRIPTION_UNPAID"
SUBSCRIPTION_PAUSED = "SUBSCRIPTION_PAUSED"
SUBSCRIPTION_CANCELED = "SUBSCRIPTION_CANCELED"
TRIAL_EXPIRED = "TRIAL_EXPIRED"
TRIAL_PERIOD_MISSING = "TRIAL_PERIOD_MISSING"
BILLING_PERIOD_ENDED = "BILLING_PERIOD_ENDED"
BILLING_PERIOD_MISSING = "BILLING_PERIOD_MISSING"
GRACE_PERIOD_EXPIRED = "GRACE_PERIOD_EXPIRED"
INVALID_SUBSCRIPTION_STATUS = "INVALID_SUBSCRIPTION_STATUS"
INVALID_BILLING_PROVIDER = "INVALID_BILLING_PROVIDER"
INVALID_BILLING_PERIOD = "INVALID_BILLING_PERIOD"
INVALID_TRIAL_PERIOD = "INVALID_TRIAL_PERIOD"
INVALID_ORGANIZATION = "INVALID_ORGANIZATION"
INVALID_SUBSCRIPTION = "INVALID_SUBSCRIPTION"
MISSING_PERIOD_END = "MISSING_PERIOD_END"

BLOCKED_STATUS_REASONS = {
    STATUS_INCOMPLETE: SUBSCRIPTION_INCOMPLETE,
    STATUS_INCOMPLETE_EXPIRED: SUBSCRIPTION_INCOMPLETE_EXPIRED,
    STATUS_UNPAID: SUBSCRIPTION_UNPAID,
    STATUS_PAUSED: SUBSCRIPTION_PAUSED,
    STATUS_CANCELED: SUBSCRIPTION_CANCELED,
    STATUS_INACTIVE: SUBSCRIPTION_INACTIVE,
}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubscriptionAccessDecision:
    allowed: bool
    status: str
    reason_code: str | None
    message: str | None
    trial_end: datetime | None
    current_period_end: datetime | None
    grace_period_end: datetime | None
    cancel_at_period_end: bool
    cancel_at: datetime | None
    cancellation_scheduled: bool
    organization_id: int | None = None
    subscription_id: int | None = None
    provider: str | None = None


class SubscriptionAccessError(Exception):

    def __init__(self, decision):
        self.decision = decision
        self.status = decision.status
        self.reason_code = decision.reason_code
        self.message = decision.message
        self.trial_end = decision.trial_end
        self.current_period_end = decision.current_period_end
        self.grace_period_end = decision.grace_period_end
        self.cancel_at_period_end = decision.cancel_at_period_end
        self.cancel_at = decision.cancel_at
        self.cancellation_scheduled = decision.cancellation_scheduled
        self.organization_id = decision.organization_id
        self.subscription_id = decision.subscription_id
        super().__init__(decision.message or decision.reason_code)


class SubscriptionValidationError(ValueError):

    def __init__(self, reason_code, message):
        self.reason_code = reason_code
        self.message = message
        super().__init__(message)


def utc_now():
    return datetime.now(timezone.utc)


def as_utc(value):
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def validate_subscription_status(status):
    normalized = (status or "").strip().lower()

    if normalized not in VALID_SUBSCRIPTION_STATUSES:
        raise SubscriptionValidationError(
            INVALID_SUBSCRIPTION_STATUS,
            "Invalid subscription status.",
        )

    return normalized


def validate_billing_provider(provider):
    normalized = (provider or "").strip().lower()

    if normalized not in VALID_BILLING_PROVIDERS:
        raise SubscriptionValidationError(
            INVALID_BILLING_PROVIDER,
            "Invalid billing provider.",
        )

    return normalized


def validate_period(start, end, reason_code):
    start = as_utc(start)
    end = as_utc(end)

    if start is not None and end is not None and end <= start:
        raise SubscriptionValidationError(
            reason_code,
            "Subscription period end must be after period start.",
        )

    return start, end


def grace_period_days():
    if has_app_context():
        return int(
            current_app.config.get(
                "SUBSCRIPTION_GRACE_PERIOD_DAYS",
                7,
            )
        )

    return 7


def scheduled_cancellation_date(subscription):
    if not subscription:
        return None

    if getattr(subscription, "cancel_at_period_end", False):
        return as_utc(subscription.current_period_end)

    return as_utc(
        getattr(subscription, "cancel_at", None)
    )


def is_cancellation_scheduled(subscription, *, now=None):
    if not subscription:
        return False

    status = (subscription.status or "").strip().lower()
    if status not in {STATUS_ACTIVE, STATUS_TRIALING, STATUS_PAST_DUE}:
        return False

    if getattr(subscription, "cancel_at_period_end", False):
        return True

    cancel_at = as_utc(getattr(subscription, "cancel_at", None))
    if cancel_at is None:
        return False

    now = as_utc(now or utc_now())
    return cancel_at > now


def get_subscription(organization):
    if not organization:
        return None

    organization_id = getattr(organization, "id", None)

    if organization_id:
        return (
            Subscription.query
            .filter_by(organization_id=organization_id)
            .first()
        )

    return getattr(organization, "subscription", None)


def get_or_create_subscription(
    organization,
    *,
    status=STATUS_INACTIVE,
    provider=PROVIDER_INTERNAL,
):
    if not organization:
        raise SubscriptionValidationError(
            INVALID_ORGANIZATION,
            "Organization is required.",
        )

    existing = get_subscription(organization)

    if existing:
        return existing

    subscription = Subscription(
        organization=organization,
        status=validate_subscription_status(status),
        provider=validate_billing_provider(provider),
    )
    db.session.add(subscription)
    db.session.flush()
    return subscription


def set_subscription_status(subscription, status):
    _require_subscription(subscription)
    previous = subscription.status
    subscription.status = validate_subscription_status(status)
    _touch(subscription)
    logger.info(
        "Subscription status changed subscription_id=%s previous=%s status=%s",
        subscription.id,
        previous,
        subscription.status,
    )
    return subscription


def set_subscription_provider(subscription, provider):
    _require_subscription(subscription)
    previous = subscription.provider
    subscription.provider = validate_billing_provider(provider)
    _touch(subscription)
    logger.info(
        "Subscription provider changed subscription_id=%s previous=%s provider=%s",
        subscription.id,
        previous,
        subscription.provider,
    )
    return subscription


def activate_subscription(
    subscription,
    *,
    period_start=None,
    period_end=None,
):
    _require_subscription(subscription)
    period_start, period_end = validate_period(
        period_start,
        period_end,
        INVALID_BILLING_PERIOD,
    )
    subscription.status = STATUS_ACTIVE
    subscription.cancel_at_period_end = False
    subscription.cancel_at = None
    subscription.canceled_at = None
    subscription.ended_at = None
    if period_start is not None:
        subscription.current_period_start = period_start
    if period_end is not None:
        subscription.current_period_end = period_end
    _touch(subscription)
    return subscription


def start_trial(
    subscription,
    *,
    trial_start,
    trial_end,
):
    _require_subscription(subscription)
    trial_start, trial_end = validate_period(
        trial_start,
        trial_end,
        INVALID_TRIAL_PERIOD,
    )

    if trial_start is None or trial_end is None:
        raise SubscriptionValidationError(
            INVALID_TRIAL_PERIOD,
            "Trial start and end are required.",
        )

    subscription.status = STATUS_TRIALING
    subscription.trial_start = trial_start
    subscription.trial_end = trial_end
    subscription.cancel_at_period_end = False
    subscription.cancel_at = None
    _touch(subscription)
    return subscription


def mark_past_due(subscription):
    _require_subscription(subscription)
    subscription.status = STATUS_PAST_DUE
    _touch(subscription)
    return subscription


def schedule_cancellation(
    subscription,
    *,
    period_end=None,
):
    _require_subscription(subscription)
    resolved_period_end = as_utc(period_end or subscription.current_period_end)

    if resolved_period_end is None:
        raise SubscriptionValidationError(
            MISSING_PERIOD_END,
            "A current period end is required to schedule cancellation.",
        )

    subscription.current_period_end = resolved_period_end
    subscription.cancel_at_period_end = True
    subscription.cancel_at = resolved_period_end
    _touch(subscription)
    return subscription


def cancel_subscription(
    subscription,
    *,
    canceled_at=None,
    ended_at=None,
):
    _require_subscription(subscription)
    canceled_at = as_utc(canceled_at or utc_now())
    ended_at = as_utc(ended_at or canceled_at)

    subscription.status = STATUS_CANCELED
    subscription.cancel_at_period_end = False
    subscription.cancel_at = None
    subscription.canceled_at = canceled_at
    subscription.ended_at = ended_at
    _touch(subscription)
    return subscription


def reactivate_subscription(
    subscription,
    *,
    period_start=None,
    period_end=None,
):
    return activate_subscription(
        subscription,
        period_start=period_start,
        period_end=period_end,
    )


def has_operational_access(
    organization,
    *,
    now=None,
):
    return get_access_decision(
        organization,
        now=now,
    ).allowed


def get_access_decision(
    organization,
    *,
    now=None,
):
    now = as_utc(now or utc_now())
    subscription = get_subscription(organization)
    organization_id = getattr(organization, "id", None)

    if subscription is None:
        return _decision(
            False,
            STATUS_INACTIVE,
            NO_SUBSCRIPTION,
            "No subscription is registered for this Organization.",
            organization_id=organization_id,
        )

    provider = (subscription.provider or "").strip().lower()
    status = (subscription.status or "").strip().lower()

    if provider not in VALID_BILLING_PROVIDERS:
        return _decision_for_subscription(
            subscription,
            False,
            status or STATUS_INACTIVE,
            INVALID_BILLING_PROVIDER,
            "The subscription billing provider is invalid.",
            now=now,
        )

    if status not in VALID_SUBSCRIPTION_STATUSES:
        return _decision_for_subscription(
            subscription,
            False,
            status or STATUS_INACTIVE,
            INVALID_SUBSCRIPTION_STATUS,
            "The subscription status is invalid.",
            now=now,
        )

    if status == STATUS_ACTIVE:
        cancellation_date = scheduled_cancellation_date(subscription)
        if cancellation_date is not None and now >= cancellation_date:
            return _decision_for_subscription(
                subscription,
                False,
                status,
                BILLING_PERIOD_ENDED,
                "The subscription billing period has ended.",
                now=now,
            )

        return _decision_for_subscription(
            subscription,
            True,
            status,
            ACCESS_ACTIVE,
            "Subscription is active.",
            now=now,
        )

    if status == STATUS_TRIALING:
        trial_end = as_utc(subscription.trial_end)
        if trial_end is None:
            return _decision_for_subscription(
                subscription,
                False,
                status,
                TRIAL_PERIOD_MISSING,
                "Trial period end is missing.",
                now=now,
            )
        if now >= trial_end:
            return _decision_for_subscription(
                subscription,
                False,
                status,
                TRIAL_EXPIRED,
                "The subscription trial has expired.",
                now=now,
            )
        return _decision_for_subscription(
            subscription,
            True,
            status,
            ACCESS_TRIALING,
            "Subscription trial is active.",
            now=now,
        )

    if status == STATUS_PAST_DUE:
        period_end = as_utc(subscription.current_period_end)
        if period_end is None:
            return _decision_for_subscription(
                subscription,
                False,
                status,
                BILLING_PERIOD_MISSING,
                "Billing period end is missing for past due subscription.",
                now=now,
            )

        grace_period_end = period_end + timedelta(days=grace_period_days())
        if now <= grace_period_end:
            return _decision_for_subscription(
                subscription,
                True,
                status,
                ACCESS_PAST_DUE_GRACE_PERIOD,
                "Subscription is past due but within the grace period.",
                grace_period_end=grace_period_end,
                now=now,
            )
        return _decision_for_subscription(
            subscription,
            False,
            status,
            GRACE_PERIOD_EXPIRED,
            "The past due grace period has expired.",
            grace_period_end=grace_period_end,
            now=now,
        )

    return _decision_for_subscription(
        subscription,
        False,
        status,
        BLOCKED_STATUS_REASONS.get(
            status,
            INVALID_SUBSCRIPTION_STATUS,
        ),
        "Subscription does not allow operational access.",
        now=now,
    )


def require_operational_access(
    organization,
    *,
    now=None,
):
    decision = get_access_decision(
        organization,
        now=now,
    )

    if not decision.allowed:
        raise SubscriptionAccessError(decision)

    return decision


def _require_subscription(subscription):
    if not subscription:
        raise SubscriptionValidationError(
            INVALID_SUBSCRIPTION,
            "Subscription is required.",
        )


def _touch(subscription):
    subscription.updated_at = utc_now()


def _decision_for_subscription(
    subscription,
    allowed,
    status,
    reason_code,
    message,
    *,
    grace_period_end=None,
    now=None,
):
    return _decision(
        allowed,
        status,
        reason_code,
        message,
        trial_end=as_utc(subscription.trial_end),
        current_period_end=as_utc(subscription.current_period_end),
        grace_period_end=grace_period_end,
        cancel_at_period_end=bool(subscription.cancel_at_period_end),
        cancel_at=as_utc(getattr(subscription, "cancel_at", None)),
        cancellation_scheduled=is_cancellation_scheduled(subscription, now=now),
        organization_id=subscription.organization_id,
        subscription_id=subscription.id,
        provider=subscription.provider,
    )


def _decision(
    allowed,
    status,
    reason_code,
    message,
    *,
    trial_end=None,
    current_period_end=None,
    grace_period_end=None,
    cancel_at_period_end=False,
    cancel_at=None,
    cancellation_scheduled=False,
    organization_id=None,
    subscription_id=None,
    provider=None,
):
    return SubscriptionAccessDecision(
        allowed=allowed,
        status=status,
        reason_code=reason_code,
        message=message,
        trial_end=trial_end,
        current_period_end=current_period_end,
        grace_period_end=grace_period_end,
        cancel_at_period_end=cancel_at_period_end,
        cancel_at=cancel_at,
        cancellation_scheduled=cancellation_scheduled,
        organization_id=organization_id,
        subscription_id=subscription_id,
        provider=provider,
    )
