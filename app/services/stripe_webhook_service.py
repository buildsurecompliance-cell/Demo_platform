import logging

from app.services.stripe_service import (
    StripeOperationError,
    retrieve_subscription,
)
from app.services.stripe_subscription_sync import (
    STRIPE_EVENT_OUT_OF_ORDER,
    apply_stripe_subscription_deleted_snapshot,
    apply_stripe_subscription_snapshot,
    resolve_organization_for_snapshot,
    resolve_organization_from_checkout_session,
    snapshot_from_stripe_subscription,
    stripe_event_created_at,
)
from app.services.subscription_service import (
    PROVIDER_STRIPE,
    get_or_create_subscription,
)


EVENT_CHECKOUT_COMPLETED = "checkout.session.completed"
EVENT_SUBSCRIPTION_CREATED = "customer.subscription.created"
EVENT_SUBSCRIPTION_UPDATED = "customer.subscription.updated"
EVENT_SUBSCRIPTION_DELETED = "customer.subscription.deleted"
EVENT_INVOICE_PAYMENT_SUCCEEDED = "invoice.payment_succeeded"
EVENT_INVOICE_PAYMENT_FAILED = "invoice.payment_failed"

SUPPORTED_EVENTS = frozenset({
    EVENT_CHECKOUT_COMPLETED,
    EVENT_SUBSCRIPTION_CREATED,
    EVENT_SUBSCRIPTION_UPDATED,
    EVENT_SUBSCRIPTION_DELETED,
    EVENT_INVOICE_PAYMENT_SUCCEEDED,
    EVENT_INVOICE_PAYMENT_FAILED,
})

WEBHOOK_EVENT_IGNORED = "ignored"
WEBHOOK_EVENT_PROCESSED = "processed"

STRIPE_WEBHOOK_OBJECT_INVALID = "STRIPE_WEBHOOK_OBJECT_INVALID"
STRIPE_WEBHOOK_ORGANIZATION_UNKNOWN = "STRIPE_WEBHOOK_ORGANIZATION_UNKNOWN"

logger = logging.getLogger(__name__)


def stripe_event_id(event):
    event_id = _object_value(event, "id")
    if not event_id:
        raise StripeOperationError(
            STRIPE_WEBHOOK_OBJECT_INVALID,
            "Stripe event ID is required.",
        )
    return str(event_id)


def stripe_event_type(event):
    event_type = _object_value(event, "type")
    if not event_type:
        raise StripeOperationError(
            STRIPE_WEBHOOK_OBJECT_INVALID,
            "Stripe event type is required.",
        )
    return str(event_type)


def process_stripe_event(event, billing_event=None):
    event_id = stripe_event_id(event)
    event_type = stripe_event_type(event)

    logger.info(
        "stripe_webhook_received event_type=%s billing_event_id=%s",
        event_type,
        getattr(billing_event, "id", None),
    )

    if event_type not in SUPPORTED_EVENTS:
        logger.info("stripe_webhook_ignored event_type=%s", event_type)
        return WEBHOOK_EVENT_IGNORED

    created_at = stripe_event_created_at(event)
    data_object = _stripe_data_object(event)

    if event_type == EVENT_CHECKOUT_COMPLETED:
        return _process_checkout_completed(
            data_object,
            billing_event,
        )

    if event_type in {
        EVENT_SUBSCRIPTION_CREATED,
        EVENT_SUBSCRIPTION_UPDATED,
    }:
        return _process_subscription_snapshot_event(
            data_object,
            event_id=event_id,
            event_created_at=created_at,
            billing_event=billing_event,
        )

    if event_type == EVENT_SUBSCRIPTION_DELETED:
        return _process_subscription_deleted(
            data_object,
            event_id=event_id,
            event_created_at=created_at,
            billing_event=billing_event,
        )

    if event_type in {
        EVENT_INVOICE_PAYMENT_SUCCEEDED,
        EVENT_INVOICE_PAYMENT_FAILED,
    }:
        return _process_invoice_event(
            data_object,
            event_id=event_id,
            event_created_at=created_at,
            billing_event=billing_event,
        )

    return WEBHOOK_EVENT_IGNORED


def _process_checkout_completed(session, billing_event):
    if _object_value(session, "mode") != "subscription":
        return WEBHOOK_EVENT_IGNORED

    organization = resolve_organization_from_checkout_session(session)
    subscription = get_or_create_subscription(organization)
    customer_id = _required_object_value(session, "customer")
    billing_subscription_id = _required_object_value(session, "subscription")

    if (
        subscription.billing_customer_id
        and subscription.billing_customer_id != customer_id
    ):
        raise StripeOperationError(
            "STRIPE_CUSTOMER_MISMATCH",
            "Stripe customer does not match local subscription.",
        )

    if (
        subscription.billing_subscription_id
        and subscription.billing_subscription_id != billing_subscription_id
    ):
        raise StripeOperationError(
            "STRIPE_SUBSCRIPTION_MISMATCH",
            "Stripe subscription does not match local subscription.",
        )

    subscription.provider = PROVIDER_STRIPE
    subscription.billing_customer_id = customer_id
    subscription.billing_subscription_id = billing_subscription_id
    _link_event(billing_event, organization, subscription)
    logger.info(
        "stripe_webhook_processed event_type=%s organization_id=%s local_subscription_id=%s",
        EVENT_CHECKOUT_COMPLETED,
        organization.id,
        subscription.id,
    )
    return WEBHOOK_EVENT_PROCESSED


def _process_subscription_snapshot_event(
    stripe_subscription,
    *,
    event_id,
    event_created_at,
    billing_event,
):
    snapshot = snapshot_from_stripe_subscription(
        stripe_subscription,
        event_id=event_id,
        event_created_at=event_created_at,
    )
    organization = resolve_organization_for_snapshot(snapshot)
    decision = apply_stripe_subscription_snapshot(
        organization,
        snapshot,
        billing_event=billing_event,
    )

    if not decision.should_apply:
        if billing_event:
            billing_event.error_message = decision.reason_code
        logger.info(
            "stripe_webhook_ignored event_type=subscription_snapshot organization_id=%s reason_code=%s",
            organization.id,
            decision.reason_code,
        )
        return WEBHOOK_EVENT_IGNORED

    return WEBHOOK_EVENT_PROCESSED


def _process_subscription_deleted(
    stripe_subscription,
    *,
    event_id,
    event_created_at,
    billing_event,
):
    subscription_id = _required_object_value(stripe_subscription, "id")
    customer_id = _required_object_value(stripe_subscription, "customer")
    snapshot = type(
        "DeletedSnapshotResolver",
        (),
        {
            "billing_subscription_id": subscription_id,
            "billing_customer_id": customer_id,
            "organization_id": _metadata_organization_id(stripe_subscription),
        },
    )()
    organization = resolve_organization_for_snapshot(snapshot)
    decision = apply_stripe_subscription_deleted_snapshot(
        organization,
        stripe_subscription,
        event_id=event_id,
        event_created_at=event_created_at,
        billing_event=billing_event,
    )

    if not decision.should_apply:
        if billing_event:
            billing_event.error_message = decision.reason_code
        if decision.reason_code == STRIPE_EVENT_OUT_OF_ORDER:
            logger.info(
                "stripe_event_out_of_order organization_id=%s billing_event_id=%s",
                organization.id,
                getattr(billing_event, "id", None),
            )
        return WEBHOOK_EVENT_IGNORED

    return WEBHOOK_EVENT_PROCESSED


def _process_invoice_event(
    invoice,
    *,
    event_id,
    event_created_at,
    billing_event,
):
    billing_subscription_id = _object_value(invoice, "subscription")
    if not billing_subscription_id:
        return WEBHOOK_EVENT_IGNORED

    remote_subscription = retrieve_subscription(str(billing_subscription_id))
    return _process_subscription_snapshot_event(
        remote_subscription,
        event_id=event_id,
        event_created_at=event_created_at,
        billing_event=billing_event,
    )


def _stripe_data_object(event):
    data = _object_value(event, "data") or {}
    obj = _object_value(data, "object")
    if not obj:
        raise StripeOperationError(
            STRIPE_WEBHOOK_OBJECT_INVALID,
            "Stripe event object is required.",
        )
    return obj


def _metadata_organization_id(obj):
    metadata = _object_value(obj, "metadata") or {}
    organization_id = _object_value(metadata, "organization_id")
    if organization_id in (None, ""):
        return None

    try:
        return int(organization_id)
    except (TypeError, ValueError) as exc:
        raise StripeOperationError(
            STRIPE_WEBHOOK_ORGANIZATION_UNKNOWN,
            "Stripe webhook Organization metadata is invalid.",
            exc,
        ) from exc


def _required_object_value(obj, key):
    value = _object_value(obj, key)
    if value is None or value == "":
        raise StripeOperationError(
            STRIPE_WEBHOOK_OBJECT_INVALID,
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
