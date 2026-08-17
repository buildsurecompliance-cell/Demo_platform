from flask import current_app

from app.services.plan_capacity import (
    PLAN_PROFESSIONAL,
    PLAN_STARTER,
    validate_plan_key,
)
from app.services.plan_catalog import (
    BILLING_LOOKUP_PROFESSIONAL_MONTHLY,
    BILLING_LOOKUP_STARTER_MONTHLY,
    get_billing_lookup_for_plan,
    get_plan_key_for_billing_lookup,
    is_self_service_plan,
)


STRIPE_PRICE_NOT_CONFIGURED = "STRIPE_PRICE_NOT_CONFIGURED"
STRIPE_PRICE_UNKNOWN = "STRIPE_PRICE_UNKNOWN"
STRIPE_PLAN_NOT_SELF_SERVICE = "STRIPE_PLAN_NOT_SELF_SERVICE"


class StripePriceMappingError(ValueError):

    def __init__(self, reason_code, message):
        self.reason_code = reason_code
        self.message = message
        super().__init__(message)


def get_stripe_price_id_for_plan(plan_key):
    plan_key = validate_plan_key(plan_key)

    if not is_self_service_plan(plan_key):
        raise StripePriceMappingError(
            STRIPE_PLAN_NOT_SELF_SERVICE,
            "This plan is not available for self-service checkout.",
        )

    mapping = _configured_plan_to_price_id()
    price_id = mapping.get(plan_key)

    if not price_id:
        raise StripePriceMappingError(
            STRIPE_PRICE_NOT_CONFIGURED,
            "Stripe price is not configured for this plan.",
        )

    return price_id


def get_plan_key_for_stripe_price_id(price_id):
    lookup_key = get_billing_lookup_for_stripe_price_id(price_id)
    return get_plan_key_for_billing_lookup(lookup_key)


def get_billing_lookup_for_stripe_price_id(price_id):
    normalized = _normalize_price_id(price_id)
    mapping = _configured_price_id_to_lookup()

    try:
        return mapping[normalized]
    except KeyError as exc:
        raise StripePriceMappingError(
            STRIPE_PRICE_UNKNOWN,
            "Stripe price is not recognized.",
        ) from exc


def configured_self_service_price_ids():
    return tuple(
        price_id
        for price_id in _configured_plan_to_price_id().values()
        if price_id
    )


def _configured_plan_to_price_id():
    return {
        PLAN_STARTER: _optional_config("STRIPE_STARTER_PRICE_ID"),
        PLAN_PROFESSIONAL: _optional_config("STRIPE_PROFESSIONAL_PRICE_ID"),
    }


def _configured_price_id_to_lookup():
    mapping = {}
    starter_price = _optional_config("STRIPE_STARTER_PRICE_ID")
    professional_price = _optional_config("STRIPE_PROFESSIONAL_PRICE_ID")

    if starter_price:
        mapping[starter_price] = BILLING_LOOKUP_STARTER_MONTHLY

    if professional_price:
        mapping[professional_price] = BILLING_LOOKUP_PROFESSIONAL_MONTHLY

    return mapping


def _optional_config(key):
    value = current_app.config.get(key)
    if value is None:
        return None

    normalized = str(value).strip()
    return normalized or None


def _normalize_price_id(price_id):
    normalized = (price_id or "").strip()

    if not normalized:
        raise StripePriceMappingError(
            STRIPE_PRICE_UNKNOWN,
            "Stripe price is not recognized.",
        )

    return normalized
