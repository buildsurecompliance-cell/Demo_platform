import logging

from app.services.billing_observability import mask_external_id
from app.services.stripe_service import (
    StripeOperationError,
    retrieve_subscription,
)
from app.services.stripe_subscription_sync import (
    apply_stripe_subscription_snapshot,
    snapshot_from_stripe_subscription,
)
from app.services.subscription_service import PROVIDER_STRIPE


STRIPE_RECONCILIATION_PROVIDER_REQUIRED = (
    "STRIPE_RECONCILIATION_PROVIDER_REQUIRED"
)
STRIPE_RECONCILIATION_SUBSCRIPTION_REQUIRED = (
    "STRIPE_RECONCILIATION_SUBSCRIPTION_REQUIRED"
)

logger = logging.getLogger(__name__)


def reconcile_subscription(
    organization,
    *,
    retrieve_remote=True,
):
    if not organization or not getattr(organization, "subscription", None):
        raise StripeOperationError(
            STRIPE_RECONCILIATION_SUBSCRIPTION_REQUIRED,
            "A local subscription is required for reconciliation.",
        )

    subscription = organization.subscription

    if subscription.provider != PROVIDER_STRIPE:
        raise StripeOperationError(
            STRIPE_RECONCILIATION_PROVIDER_REQUIRED,
            "Stripe provider is required for reconciliation.",
        )

    if not subscription.billing_subscription_id:
        raise StripeOperationError(
            STRIPE_RECONCILIATION_SUBSCRIPTION_REQUIRED,
            "Stripe subscription ID is required for reconciliation.",
        )

    logger.info(
        "stripe_reconciliation_started organization_id=%s local_subscription_id=%s billing_subscription_id=%s",
        organization.id,
        subscription.id,
        mask_external_id(subscription.billing_subscription_id),
    )

    remote = (
        retrieve_subscription(subscription.billing_subscription_id)
        if retrieve_remote
        else None
    )

    if remote is None:
        raise StripeOperationError(
            STRIPE_RECONCILIATION_SUBSCRIPTION_REQUIRED,
            "Remote Stripe subscription is required for reconciliation.",
        )

    snapshot = snapshot_from_stripe_subscription(remote)
    decision = apply_stripe_subscription_snapshot(
        organization,
        snapshot,
        enforce_event_order=False,
    )

    logger.info(
        "stripe_reconciliation_completed organization_id=%s local_subscription_id=%s status=%s",
        organization.id,
        subscription.id,
        organization.subscription.status,
    )
    return decision


def reconcile_subscription_by_billing_id(billing_subscription_id):
    from app.models import Subscription

    subscription = (
        Subscription.query
        .filter_by(billing_subscription_id=billing_subscription_id)
        .first()
    )
    if not subscription:
        raise StripeOperationError(
            STRIPE_RECONCILIATION_SUBSCRIPTION_REQUIRED,
            "Local Stripe subscription was not found.",
        )

    return reconcile_subscription(subscription.organization)
