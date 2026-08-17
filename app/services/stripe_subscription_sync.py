import logging

from dataclasses import dataclass
from datetime import datetime

from app.extensions import db
from app.models import Organization, Subscription
from app.services.billing_observability import as_utc, safe_reason_code, utc_now
from app.services.plan_capacity import set_organization_plan
from app.services.stripe_price_mapping import get_plan_key_for_stripe_price_id
from app.services.stripe_service import (
    StripeOperationError,
    stripe_timestamp_to_utc_datetime,
)
from app.services.subscription_service import (
    PROVIDER_STRIPE,
    STATUS_CANCELED,
    VALID_SUBSCRIPTION_STATUSES,
    get_or_create_subscription,
)


STRIPE_EVENT_APPLY = "STRIPE_EVENT_APPLY"
STRIPE_EVENT_DUPLICATE = "STRIPE_EVENT_DUPLICATE"
STRIPE_EVENT_OUT_OF_ORDER = "STRIPE_EVENT_OUT_OF_ORDER"
STRIPE_EVENT_TIMESTAMP_MISSING = "STRIPE_EVENT_TIMESTAMP_MISSING"
STRIPE_EVENT_CONFLICT = "STRIPE_EVENT_CONFLICT"

STRIPE_ORGANIZATION_MISMATCH = "STRIPE_ORGANIZATION_MISMATCH"
STRIPE_CUSTOMER_MISMATCH = "STRIPE_CUSTOMER_MISMATCH"
STRIPE_SUBSCRIPTION_MISMATCH = "STRIPE_SUBSCRIPTION_MISMATCH"
STRIPE_METADATA_MISMATCH = "STRIPE_METADATA_MISMATCH"
STRIPE_SNAPSHOT_INVALID = "STRIPE_SNAPSHOT_INVALID"
STRIPE_PRICE_ITEM_INVALID = "STRIPE_PRICE_ITEM_INVALID"
STRIPE_STATUS_UNKNOWN = "STRIPE_STATUS_UNKNOWN"

MAX_SYNC_ERROR_LENGTH = 500

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StripeEventApplicationDecision:
    should_apply: bool
    reason_code: str | None = None


@dataclass(frozen=True)
class StripeSubscriptionSnapshot:
    billing_subscription_id: str
    billing_customer_id: str
    billing_price_id: str
    status: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    trial_start: datetime | None
    trial_end: datetime | None
    cancel_at_period_end: bool
    cancel_at: datetime | None
    canceled_at: datetime | None
    ended_at: datetime | None
    organization_id: int | None
    event_id: str | None = None
    event_created_at: datetime | None = None


def stripe_event_created_at(event):
    created = _object_value(event, "created")

    if created is None:
        raise StripeOperationError(
            STRIPE_EVENT_TIMESTAMP_MISSING,
            "Stripe event created timestamp is required.",
        )

    return stripe_timestamp_to_utc_datetime(created)


def should_apply_stripe_event(
    subscription,
    *,
    event_id,
    event_created_at,
):
    event_id = (event_id or "").strip()
    event_created_at = _normalize_event_created_at(event_created_at)

    if not event_id or event_created_at is None:
        return StripeEventApplicationDecision(
            False,
            STRIPE_EVENT_TIMESTAMP_MISSING,
        )

    previous_created_at = as_utc(
        getattr(subscription, "stripe_event_created_at", None)
    )
    previous_event_id = (
        getattr(subscription, "stripe_event_id", None) or ""
    ).strip()

    if previous_created_at is None:
        return StripeEventApplicationDecision(True, STRIPE_EVENT_APPLY)

    if event_created_at > previous_created_at:
        return StripeEventApplicationDecision(True, STRIPE_EVENT_APPLY)

    if event_created_at < previous_created_at:
        return StripeEventApplicationDecision(
            False,
            STRIPE_EVENT_OUT_OF_ORDER,
        )

    if event_id == previous_event_id:
        return StripeEventApplicationDecision(
            False,
            STRIPE_EVENT_DUPLICATE,
        )

    return StripeEventApplicationDecision(
        False,
        STRIPE_EVENT_CONFLICT,
    )


def snapshot_from_stripe_subscription(
    stripe_subscription,
    *,
    event_id=None,
    event_created_at=None,
):
    billing_subscription_id = _required_object_value(
        stripe_subscription,
        "id",
    )
    billing_customer_id = _required_object_value(
        stripe_subscription,
        "customer",
    )
    status = _normalize_status(
        _required_object_value(stripe_subscription, "status")
    )
    subscription_item = _single_subscription_item(stripe_subscription)
    billing_price_id = _price_id_from_subscription_item(subscription_item)
    organization_id = _metadata_organization_id(stripe_subscription)

    return StripeSubscriptionSnapshot(
        billing_subscription_id=billing_subscription_id,
        billing_customer_id=billing_customer_id,
        billing_price_id=billing_price_id,
        status=status,
        current_period_start=_subscription_period_datetime(
            stripe_subscription,
            subscription_item,
            "current_period_start",
        ),
        current_period_end=_subscription_period_datetime(
            stripe_subscription,
            subscription_item,
            "current_period_end",
        ),
        trial_start=_stripe_datetime(stripe_subscription, "trial_start"),
        trial_end=_stripe_datetime(stripe_subscription, "trial_end"),
        cancel_at_period_end=bool(
            _object_value(stripe_subscription, "cancel_at_period_end")
        ),
        cancel_at=_stripe_datetime(stripe_subscription, "cancel_at"),
        canceled_at=_stripe_datetime(stripe_subscription, "canceled_at"),
        ended_at=_stripe_datetime(stripe_subscription, "ended_at"),
        organization_id=organization_id,
        event_id=event_id,
        event_created_at=_normalize_event_created_at(event_created_at),
    )


def apply_stripe_subscription_snapshot(
    organization,
    snapshot,
    *,
    billing_event=None,
    enforce_event_order=True,
):
    _require_snapshot(snapshot)
    plan_key = get_plan_key_for_stripe_price_id(snapshot.billing_price_id)
    subscription = get_or_create_subscription(organization)

    _validate_snapshot_ownership(
        organization,
        subscription,
        snapshot,
    )

    if enforce_event_order:
        decision = should_apply_stripe_event(
            subscription,
            event_id=snapshot.event_id,
            event_created_at=snapshot.event_created_at,
        )
        if not decision.should_apply:
            logger.info(
                "stripe_event_out_of_order organization_id=%s local_subscription_id=%s reason_code=%s",
                organization.id,
                subscription.id,
                decision.reason_code,
            )
            _link_event(billing_event, organization, subscription)
            return decision

    status_before = subscription.status
    plan_before = organization.plan_key

    subscription.provider = PROVIDER_STRIPE
    subscription.status = snapshot.status
    subscription.billing_customer_id = snapshot.billing_customer_id
    subscription.billing_subscription_id = snapshot.billing_subscription_id
    subscription.billing_price_id = snapshot.billing_price_id
    subscription.current_period_start = snapshot.current_period_start
    subscription.current_period_end = snapshot.current_period_end
    subscription.trial_start = snapshot.trial_start
    subscription.trial_end = snapshot.trial_end
    subscription.cancel_at_period_end = snapshot.cancel_at_period_end
    subscription.cancel_at = snapshot.cancel_at
    subscription.canceled_at = snapshot.canceled_at
    subscription.ended_at = snapshot.ended_at
    subscription.stripe_last_synced_at = utc_now()
    subscription.stripe_sync_error = None

    if enforce_event_order:
        subscription.stripe_event_created_at = snapshot.event_created_at
        subscription.stripe_event_id = snapshot.event_id

    set_organization_plan(organization, plan_key)
    _link_event(billing_event, organization, subscription)

    logger.info(
        "stripe_subscription_snapshot_applied organization_id=%s local_subscription_id=%s status_before=%s status_after=%s plan_before=%s plan_after=%s",
        organization.id,
        subscription.id,
        status_before,
        subscription.status,
        plan_before,
        organization.plan_key,
    )

    return StripeEventApplicationDecision(True, STRIPE_EVENT_APPLY)


def apply_stripe_subscription_deleted_snapshot(
    organization,
    stripe_subscription,
    *,
    event_id,
    event_created_at,
    billing_event=None,
):
    subscription_id = _required_object_value(stripe_subscription, "id")
    customer_id = _required_object_value(stripe_subscription, "customer")
    organization_id = _metadata_organization_id(stripe_subscription)
    subscription_item = _optional_single_subscription_item(stripe_subscription)
    snapshot = StripeSubscriptionSnapshot(
        billing_subscription_id=subscription_id,
        billing_customer_id=customer_id,
        billing_price_id="",
        status=STATUS_CANCELED,
        current_period_start=_subscription_period_datetime(
            stripe_subscription,
            subscription_item,
            "current_period_start",
        ),
        current_period_end=_subscription_period_datetime(
            stripe_subscription,
            subscription_item,
            "current_period_end",
        ),
        trial_start=_stripe_datetime(stripe_subscription, "trial_start"),
        trial_end=_stripe_datetime(stripe_subscription, "trial_end"),
        cancel_at_period_end=False,
        cancel_at=None,
        canceled_at=(
            _stripe_datetime(stripe_subscription, "canceled_at")
            or _stripe_datetime(stripe_subscription, "ended_at")
        ),
        ended_at=_stripe_datetime(stripe_subscription, "ended_at"),
        organization_id=organization_id,
        event_id=event_id,
        event_created_at=_normalize_event_created_at(event_created_at),
    )
    subscription = get_or_create_subscription(organization)
    _validate_snapshot_ownership(
        organization,
        subscription,
        snapshot,
        require_price=False,
    )
    decision = should_apply_stripe_event(
        subscription,
        event_id=event_id,
        event_created_at=event_created_at,
    )
    if not decision.should_apply:
        _link_event(billing_event, organization, subscription)
        return decision

    subscription.provider = PROVIDER_STRIPE
    subscription.status = STATUS_CANCELED
    subscription.billing_customer_id = snapshot.billing_customer_id
    subscription.billing_subscription_id = snapshot.billing_subscription_id
    subscription.current_period_start = snapshot.current_period_start
    subscription.current_period_end = snapshot.current_period_end
    subscription.trial_start = snapshot.trial_start
    subscription.trial_end = snapshot.trial_end
    subscription.cancel_at_period_end = False
    subscription.cancel_at = None
    subscription.canceled_at = snapshot.canceled_at
    subscription.ended_at = snapshot.ended_at
    subscription.stripe_event_created_at = snapshot.event_created_at
    subscription.stripe_event_id = snapshot.event_id
    subscription.stripe_last_synced_at = utc_now()
    subscription.stripe_sync_error = None
    _link_event(billing_event, organization, subscription)
    return StripeEventApplicationDecision(True, STRIPE_EVENT_APPLY)


def resolve_organization_for_snapshot(snapshot):
    candidates = []

    by_subscription_id = (
        Subscription.query
        .filter_by(
            billing_subscription_id=snapshot.billing_subscription_id,
        )
        .first()
    )
    if by_subscription_id:
        candidates.append(by_subscription_id.organization)

    by_customer_id = (
        Subscription.query
        .filter_by(billing_customer_id=snapshot.billing_customer_id)
        .first()
    )
    if by_customer_id:
        candidates.append(by_customer_id.organization)

    if snapshot.organization_id is not None:
        organization = db.session.get(Organization, snapshot.organization_id)
        if not organization:
            raise StripeOperationError(
                STRIPE_ORGANIZATION_MISMATCH,
                "Stripe metadata Organization is unknown.",
            )
        candidates.append(organization)

    unique_ids = {
        organization.id
        for organization in candidates
        if organization
    }

    if len(unique_ids) > 1:
        raise StripeOperationError(
            STRIPE_METADATA_MISMATCH,
            "Stripe subscription ownership is inconsistent.",
        )

    if not candidates:
        raise StripeOperationError(
            STRIPE_ORGANIZATION_MISMATCH,
            "Stripe subscription Organization could not be resolved.",
        )

    return candidates[0]


def resolve_organization_from_checkout_session(session):
    metadata = _object_value(session, "metadata") or {}
    organization_id = (
        _object_value(metadata, "organization_id")
        or _object_value(session, "client_reference_id")
    )

    try:
        organization_id = int(organization_id)
    except (TypeError, ValueError) as exc:
        raise StripeOperationError(
            STRIPE_ORGANIZATION_MISMATCH,
            "Stripe checkout Organization could not be resolved.",
            exc,
        ) from exc

    organization = db.session.get(Organization, organization_id)
    if not organization:
        raise StripeOperationError(
            STRIPE_ORGANIZATION_MISMATCH,
            "Stripe checkout Organization could not be resolved.",
        )

    return organization


def record_subscription_sync_error(subscription, exc):
    if not subscription:
        return

    subscription.stripe_sync_error = safe_reason_code(exc)[:MAX_SYNC_ERROR_LENGTH]
    subscription.stripe_last_synced_at = utc_now()


def _validate_snapshot_ownership(
    organization,
    subscription,
    snapshot,
    *,
    require_price=True,
):
    if snapshot.organization_id is not None and snapshot.organization_id != organization.id:
        raise StripeOperationError(
            STRIPE_METADATA_MISMATCH,
            "Stripe metadata Organization does not match local Organization.",
        )

    existing_customer = (
        Subscription.query
        .filter_by(billing_customer_id=snapshot.billing_customer_id)
        .first()
    )
    if existing_customer and existing_customer.organization_id != organization.id:
        raise StripeOperationError(
            STRIPE_CUSTOMER_MISMATCH,
            "Stripe customer belongs to another Organization.",
        )

    if (
        subscription.billing_customer_id
        and subscription.billing_customer_id != snapshot.billing_customer_id
    ):
        raise StripeOperationError(
            STRIPE_CUSTOMER_MISMATCH,
            "Stripe customer does not match local subscription.",
        )

    existing_subscription = (
        Subscription.query
        .filter_by(
            billing_subscription_id=snapshot.billing_subscription_id,
        )
        .first()
    )
    if existing_subscription and existing_subscription.organization_id != organization.id:
        raise StripeOperationError(
            STRIPE_SUBSCRIPTION_MISMATCH,
            "Stripe subscription belongs to another Organization.",
        )

    if (
        subscription.billing_subscription_id
        and subscription.billing_subscription_id
        != snapshot.billing_subscription_id
    ):
        raise StripeOperationError(
            STRIPE_SUBSCRIPTION_MISMATCH,
            "Stripe subscription does not match local subscription.",
        )

    if require_price and not snapshot.billing_price_id:
        raise StripeOperationError(
            STRIPE_PRICE_ITEM_INVALID,
            "Stripe price is required.",
        )


def _require_snapshot(snapshot):
    if not isinstance(snapshot, StripeSubscriptionSnapshot):
        raise StripeOperationError(
            STRIPE_SNAPSHOT_INVALID,
            "Stripe subscription snapshot is required.",
        )


def _single_price_id(stripe_subscription):
    return _price_id_from_subscription_item(
        _single_subscription_item(stripe_subscription)
    )


def _single_subscription_item(stripe_subscription):
    items = _object_value(stripe_subscription, "items") or {}
    data = _object_value(items, "data") or []

    if len(data) != 1:
        raise StripeOperationError(
            STRIPE_PRICE_ITEM_INVALID,
            "Stripe subscription must contain exactly one price item.",
        )

    return data[0]


def _optional_single_subscription_item(stripe_subscription):
    items = _object_value(stripe_subscription, "items") or {}
    data = _object_value(items, "data") or []

    if not data:
        return None

    if len(data) != 1:
        raise StripeOperationError(
            STRIPE_PRICE_ITEM_INVALID,
            "Stripe subscription must contain exactly one price item.",
        )

    return data[0]


def _price_id_from_subscription_item(subscription_item):
    price = _object_value(subscription_item, "price") or {}
    return _required_object_value(price, "id")


def _normalize_status(status):
    normalized = str(status or "").strip().lower()

    if normalized not in VALID_SUBSCRIPTION_STATUSES:
        raise StripeOperationError(
            STRIPE_STATUS_UNKNOWN,
            "Stripe subscription status is not supported.",
        )

    return normalized


def _metadata_organization_id(obj):
    metadata = _object_value(obj, "metadata") or {}
    organization_id = _object_value(metadata, "organization_id")
    if organization_id in (None, ""):
        return None

    try:
        return int(organization_id)
    except (TypeError, ValueError) as exc:
        raise StripeOperationError(
            STRIPE_METADATA_MISMATCH,
            "Stripe metadata Organization is invalid.",
            exc,
        ) from exc


def _stripe_datetime(obj, key):
    return stripe_timestamp_to_utc_datetime(
        _object_value(obj, key)
    )


def _subscription_period_datetime(stripe_subscription, subscription_item, key):
    return (
        _stripe_datetime(stripe_subscription, key)
        or _stripe_datetime(subscription_item, key)
    )


def _normalize_event_created_at(value):
    if value is None:
        return None

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return stripe_timestamp_to_utc_datetime(value)

    return as_utc(value)


def _required_object_value(obj, key):
    value = _object_value(obj, key)
    if value is None or value == "":
        raise StripeOperationError(
            STRIPE_SNAPSHOT_INVALID,
            f"Stripe object field {key} is required.",
        )
    return str(value)


def _link_event(billing_event, organization, subscription):
    if not billing_event:
        return

    billing_event.organization = organization
    billing_event.subscription = subscription


def _object_value(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)

    return getattr(obj, key, default)
