import importlib
import logging
import secrets

from flask import current_app, url_for

from app.services.billing_observability import mask_external_id
from app.services.organizations import normalize_email
from app.services.plan_capacity import validate_plan_key
from app.services.plan_catalog import (
    get_billing_lookup_for_plan,
    is_self_service_plan,
)
from app.services.stripe_price_mapping import (
    StripePriceMappingError,
    get_stripe_price_id_for_plan,
)
from app.services.subscription_service import (
    PROVIDER_STRIPE,
    STATUS_ACTIVE,
    STATUS_INACTIVE,
    STATUS_PAST_DUE,
    STATUS_TRIALING,
    get_or_create_subscription,
)


STRIPE_NOT_CONFIGURED = "STRIPE_NOT_CONFIGURED"
STRIPE_CUSTOMER_CREATE_FAILED = "STRIPE_CUSTOMER_CREATE_FAILED"
STRIPE_CHECKOUT_CREATE_FAILED = "STRIPE_CHECKOUT_CREATE_FAILED"
STRIPE_PORTAL_CREATE_FAILED = "STRIPE_PORTAL_CREATE_FAILED"
STRIPE_WEBHOOK_SIGNATURE_INVALID = "STRIPE_WEBHOOK_SIGNATURE_INVALID"
STRIPE_SUBSCRIPTION_RETRIEVE_FAILED = "STRIPE_SUBSCRIPTION_RETRIEVE_FAILED"
STRIPE_CUSTOMER_REQUIRED = "STRIPE_CUSTOMER_REQUIRED"
STRIPE_SDK_UNAVAILABLE = "STRIPE_SDK_UNAVAILABLE"
STRIPE_PROVIDER_REQUIRED = "STRIPE_PROVIDER_REQUIRED"
STRIPE_EXISTING_SUBSCRIPTION_REQUIRES_PORTAL = (
    "STRIPE_EXISTING_SUBSCRIPTION_REQUIRES_PORTAL"
)

logger = logging.getLogger(__name__)


class StripeConfigurationError(Exception):

    def __init__(self, reason_code, message, original_exception=None):
        self.reason_code = reason_code
        self.message = message
        self.original_exception = original_exception
        super().__init__(message)


class StripeOperationError(Exception):

    def __init__(self, reason_code, message, original_exception=None):
        self.reason_code = reason_code
        self.message = message
        self.original_exception = original_exception
        super().__init__(message)


class StripeWebhookError(Exception):

    def __init__(self, reason_code, message, original_exception=None):
        self.reason_code = reason_code
        self.message = message
        self.original_exception = original_exception
        super().__init__(message)


def is_stripe_configured():
    return bool(_billing_provider_is_stripe() and _secret_key())


def is_stripe_checkout_configured():
    return bool(
        is_stripe_configured()
        and current_app.config.get("STRIPE_STARTER_PRICE_ID")
        and current_app.config.get("STRIPE_PROFESSIONAL_PRICE_ID")
    )


def is_stripe_portal_configured():
    return is_stripe_configured()


def get_stripe_configuration_status():
    if not _billing_provider_is_stripe():
        return {
            "enabled": False,
            "ready": True,
            "missing": [],
        }

    required = (
        ("STRIPE_SECRET_KEY", "missing_secret_key"),
        ("STRIPE_WEBHOOK_SECRET", "missing_webhook_secret"),
        ("STRIPE_STARTER_PRICE_ID", "missing_starter_price"),
        ("STRIPE_PROFESSIONAL_PRICE_ID", "missing_professional_price"),
        ("BILLING_SUCCESS_URL", "missing_success_url"),
        ("BILLING_CANCEL_URL", "missing_cancel_url"),
        ("BILLING_PORTAL_RETURN_URL", "missing_portal_return_url"),
    )
    missing = [
        reason_code
        for key, reason_code in required
        if not _optional_config(key)
    ]

    return {
        "enabled": True,
        "ready": not missing,
        "missing": missing,
    }


def configure_stripe():
    secret_key = _require_secret_key()
    stripe = _stripe_module()
    stripe.api_key = secret_key

    api_version = _optional_config("STRIPE_API_VERSION")
    if api_version:
        stripe.api_version = api_version

    return stripe


def get_or_create_customer(
    organization,
    *,
    owner_user=None,
):
    _require_organization(organization)
    stripe = configure_stripe()
    subscription = get_or_create_subscription(
        organization,
        status=STATUS_INACTIVE,
    )

    if subscription.billing_customer_id:
        return subscription.billing_customer_id

    refreshed = get_or_create_subscription(
        organization,
        status=STATUS_INACTIVE,
    )
    if refreshed.billing_customer_id:
        return refreshed.billing_customer_id

    params = {
        "metadata": {
            "organization_id": str(organization.id),
        },
    }

    owner_email = _owner_email(owner_user)
    if owner_email:
        params["email"] = owner_email

    try:
        customer = stripe.Customer.create(
            **params,
            idempotency_key=_customer_idempotency_key(organization),
        )
    except Exception as exc:
        raise StripeOperationError(
            STRIPE_CUSTOMER_CREATE_FAILED,
            "Stripe customer could not be created.",
            exc,
        ) from exc

    customer_id = _object_value(customer, "id")
    if not customer_id:
        raise StripeOperationError(
            STRIPE_CUSTOMER_CREATE_FAILED,
            "Stripe customer response was invalid.",
        )

    subscription.billing_customer_id = customer_id
    subscription.provider = PROVIDER_STRIPE
    logger.info(
        "stripe_customer_created organization_id=%s local_subscription_id=%s customer_id=%s",
        organization.id,
        subscription.id,
        mask_external_id(customer_id),
    )
    return customer_id


def create_checkout_session(
    organization,
    *,
    plan_key,
    success_url=None,
    cancel_url=None,
    owner_user=None,
):
    _require_organization(organization)
    plan_key = validate_plan_key(plan_key)

    if not is_self_service_plan(plan_key):
        raise StripePriceMappingError(
            "STRIPE_PLAN_NOT_SELF_SERVICE",
            "This plan is not available for self-service checkout.",
        )

    _prevent_parallel_subscription_checkout(organization)
    stripe = configure_stripe()
    price_id = get_stripe_price_id_for_plan(plan_key)
    customer_id = get_or_create_customer(
        organization,
        owner_user=owner_user,
    )
    billing_lookup_key = get_billing_lookup_for_plan(plan_key)

    resolved_success_url = success_url or _configured_or_url(
        "BILLING_SUCCESS_URL",
        "billing.checkout_success",
    )
    resolved_cancel_url = cancel_url or _configured_or_url(
        "BILLING_CANCEL_URL",
        "billing.checkout_canceled",
    )

    metadata = {
        "organization_id": str(organization.id),
        "plan_key": plan_key,
        "billing_lookup_key": billing_lookup_key,
    }

    try:
        checkout_session = stripe.checkout.Session.create(
            mode=current_app.config.get(
                "STRIPE_CHECKOUT_MODE",
                "subscription",
            ),
            customer=customer_id,
            client_reference_id=str(organization.id),
            line_items=[
                {
                    "price": price_id,
                    "quantity": 1,
                }
            ],
            success_url=resolved_success_url,
            cancel_url=resolved_cancel_url,
            metadata=metadata,
            subscription_data={
                "metadata": metadata,
            },
            idempotency_key=_checkout_idempotency_key(organization, plan_key),
        )
        logger.info(
            "stripe_checkout_created organization_id=%s local_subscription_id=%s plan_key=%s customer_id=%s",
            organization.id,
            organization.subscription.id,
            plan_key,
            mask_external_id(customer_id),
        )
        return checkout_session
    except Exception as exc:
        raise StripeOperationError(
            STRIPE_CHECKOUT_CREATE_FAILED,
            "Stripe checkout could not be created.",
            exc,
        ) from exc


def create_customer_portal_session(
    organization,
    *,
    return_url=None,
):
    _require_organization(organization)
    stripe = configure_stripe()
    subscription = get_or_create_subscription(
        organization,
        status=STATUS_INACTIVE,
    )

    if not subscription.billing_customer_id:
        raise StripeOperationError(
            STRIPE_CUSTOMER_REQUIRED,
            "A Stripe customer is required before opening the billing portal.",
        )

    if subscription.provider != PROVIDER_STRIPE:
        raise StripeOperationError(
            STRIPE_PROVIDER_REQUIRED,
            "Stripe provider is required before opening the billing portal.",
        )

    resolved_return_url = return_url or _configured_or_url(
        "BILLING_PORTAL_RETURN_URL",
        "auth.subscribe",
    )

    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=subscription.billing_customer_id,
            return_url=resolved_return_url,
        )
        logger.info(
            "stripe_portal_created organization_id=%s local_subscription_id=%s customer_id=%s",
            organization.id,
            subscription.id,
            mask_external_id(subscription.billing_customer_id),
        )
        return portal_session
    except Exception as exc:
        raise StripeOperationError(
            STRIPE_PORTAL_CREATE_FAILED,
            "Stripe customer portal could not be created.",
            exc,
        ) from exc


def construct_webhook_event(payload, signature_header):
    webhook_secret = _optional_config("STRIPE_WEBHOOK_SECRET")

    if not webhook_secret:
        raise StripeConfigurationError(
            STRIPE_NOT_CONFIGURED,
            "Stripe webhook secret is not configured.",
        )

    if not signature_header:
        raise StripeWebhookError(
            STRIPE_WEBHOOK_SIGNATURE_INVALID,
            "Stripe signature is missing.",
        )

    stripe = configure_stripe()

    try:
        return stripe.Webhook.construct_event(
            payload,
            signature_header,
            webhook_secret,
        )
    except Exception as exc:
        raise StripeWebhookError(
            STRIPE_WEBHOOK_SIGNATURE_INVALID,
            "Stripe webhook signature is invalid.",
            exc,
        ) from exc


def retrieve_subscription(billing_subscription_id):
    if not billing_subscription_id:
        raise StripeOperationError(
            STRIPE_SUBSCRIPTION_RETRIEVE_FAILED,
            "Stripe subscription ID is required.",
        )

    stripe = configure_stripe()

    try:
        return stripe.Subscription.retrieve(billing_subscription_id)
    except Exception as exc:
        raise StripeOperationError(
            STRIPE_SUBSCRIPTION_RETRIEVE_FAILED,
            "Stripe subscription could not be retrieved.",
            exc,
        ) from exc


def stripe_timestamp_to_utc_datetime(value):
    from datetime import datetime, timezone

    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StripeOperationError(
            "STRIPE_INVALID_TIMESTAMP",
            "Stripe timestamp is invalid.",
        )

    return datetime.fromtimestamp(value, tz=timezone.utc)


def _stripe_module():
    try:
        return importlib.import_module("stripe")
    except ImportError as exc:
        raise StripeConfigurationError(
            STRIPE_SDK_UNAVAILABLE,
            "Stripe SDK is not installed.",
            exc,
        ) from exc


def _require_secret_key():
    if not _billing_provider_is_stripe():
        raise StripeConfigurationError(
            STRIPE_NOT_CONFIGURED,
            "Stripe is not the active billing provider.",
        )

    secret_key = _secret_key()

    if not secret_key:
        raise StripeConfigurationError(
            STRIPE_NOT_CONFIGURED,
            "Stripe is not configured.",
        )

    return secret_key


def _secret_key():
    return _optional_config("STRIPE_SECRET_KEY")


def _billing_provider_is_stripe():
    return (
        (_optional_config("BILLING_PROVIDER") or "").lower()
        == PROVIDER_STRIPE
    )


def _optional_config(key):
    value = current_app.config.get(key)
    if value is None:
        return None

    normalized = str(value).strip()
    return normalized or None


def _require_organization(organization):
    if not organization or not getattr(organization, "id", None):
        raise StripeOperationError(
            "STRIPE_ORGANIZATION_REQUIRED",
            "Organization is required.",
        )


def _owner_email(owner_user):
    email = getattr(owner_user, "email", None)
    if email:
        return normalize_email(email)

    return None


def _configured_or_url(config_key, endpoint):
    configured = _optional_config(config_key)
    if configured:
        return configured

    return url_for(endpoint, _external=True)


def _customer_idempotency_key(organization):
    return f"buildsure:customer:create:organization:{organization.id}"


def _checkout_idempotency_key(organization, plan_key):
    token = secrets.token_urlsafe(12)
    return build_checkout_idempotency_key(
        organization.id,
        plan_key,
        token,
    )


def build_checkout_idempotency_key(
    organization_id,
    plan_key,
    attempt_token,
):
    token = (attempt_token or "").strip()
    if not token:
        token = secrets.token_urlsafe(12)

    return (
        "buildsure:checkout:create:"
        f"organization:{organization_id}:plan:{plan_key}:{token}"
    )


def _prevent_parallel_subscription_checkout(organization):
    subscription = get_or_create_subscription(
        organization,
        status=STATUS_INACTIVE,
    )

    if (
        subscription.provider == PROVIDER_STRIPE
        and subscription.billing_subscription_id
        and subscription.status in {
            STATUS_ACTIVE,
            STATUS_TRIALING,
            STATUS_PAST_DUE,
        }
    ):
        raise StripeOperationError(
            STRIPE_EXISTING_SUBSCRIPTION_REQUIRES_PORTAL,
            "Existing Stripe subscriptions must be managed through the billing portal.",
        )


def _object_value(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)

    return getattr(obj, key, default)
