from dataclasses import dataclass
from types import MappingProxyType

from app.services.plan_capacity import (
    PLAN_DEFINITIONS,
    PLAN_ENTERPRISE,
    PLAN_PROFESSIONAL,
    PLAN_STARTER,
    validate_plan_key,
)


BILLING_LOOKUP_STARTER_MONTHLY = "starter_monthly"
BILLING_LOOKUP_PROFESSIONAL_MONTHLY = "professional_monthly"
BILLING_LOOKUP_ENTERPRISE = "enterprise_custom"


class UnknownBillingLookupError(ValueError):
    pass


@dataclass(frozen=True)
class PlanCatalogEntry:
    plan_key: str
    display_name: str
    description: str
    monthly_price_cents: int | None
    billing_lookup_key: str
    display_order: int
    self_service: bool
    max_projects: int | None
    max_subcontractors: int | None
    all_tools: bool = True
    unlimited_users: bool = True
    call_to_action: str = "Select plan"


_PLAN_METADATA = {
    PLAN_STARTER: {
        "description": "For small teams starting document compliance.",
        "monthly_price_cents": None,
        "billing_lookup_key": BILLING_LOOKUP_STARTER_MONTHLY,
        "display_order": 10,
        "self_service": True,
        "call_to_action": "Select Starter",
    },
    PLAN_PROFESSIONAL: {
        "description": "For growing teams managing more projects and subcontractors.",
        "monthly_price_cents": None,
        "billing_lookup_key": BILLING_LOOKUP_PROFESSIONAL_MONTHLY,
        "display_order": 20,
        "self_service": True,
        "call_to_action": "Select Professional",
    },
    PLAN_ENTERPRISE: {
        "description": "For larger organizations with unlimited capacity.",
        "monthly_price_cents": None,
        "billing_lookup_key": BILLING_LOOKUP_ENTERPRISE,
        "display_order": 30,
        "self_service": False,
        "call_to_action": "Contact sales",
    },
}

_BILLING_LOOKUP_TO_PLAN_KEY = {
    metadata["billing_lookup_key"]: plan_key
    for plan_key, metadata in _PLAN_METADATA.items()
}

BILLING_LOOKUP_TO_PLAN_KEY = MappingProxyType(_BILLING_LOOKUP_TO_PLAN_KEY)


def get_plan_catalog():
    entries = [
        _entry_for_plan(plan_key)
        for plan_key in PLAN_DEFINITIONS
    ]
    return tuple(
        sorted(
            entries,
            key=lambda entry: entry.display_order,
        )
    )


def get_plan_catalog_entry(plan_key):
    return _entry_for_plan(
        validate_plan_key(plan_key)
    )


def get_plan_key_for_billing_lookup(lookup_key):
    normalized = _normalize_lookup_key(lookup_key)

    try:
        return BILLING_LOOKUP_TO_PLAN_KEY[normalized]
    except KeyError as exc:
        raise UnknownBillingLookupError(
            "Unknown billing lookup key."
        ) from exc


def get_billing_lookup_for_plan(plan_key):
    return get_plan_catalog_entry(plan_key).billing_lookup_key


def is_self_service_plan(plan_key):
    return get_plan_catalog_entry(plan_key).self_service


def format_plan_price(plan_key):
    entry = get_plan_catalog_entry(plan_key)

    if entry.monthly_price_cents is None:
        if entry.self_service:
            return "Contact BuildSure"
        return "Contact sales"

    dollars = entry.monthly_price_cents / 100
    return f"${dollars:,.0f}/mo"


def _entry_for_plan(plan_key):
    plan = PLAN_DEFINITIONS[
        validate_plan_key(plan_key)
    ]
    metadata = _PLAN_METADATA[plan.key]

    return PlanCatalogEntry(
        plan_key=plan.key,
        display_name=plan.name,
        description=metadata["description"],
        monthly_price_cents=metadata["monthly_price_cents"],
        billing_lookup_key=metadata["billing_lookup_key"],
        display_order=metadata["display_order"],
        self_service=metadata["self_service"],
        max_projects=plan.max_projects,
        max_subcontractors=plan.max_subcontractors,
        call_to_action=metadata["call_to_action"],
    )


def _normalize_lookup_key(lookup_key):
    normalized = (lookup_key or "").strip()

    if not normalized or len(normalized) > 100:
        raise UnknownBillingLookupError(
            "Unknown billing lookup key."
        )

    return normalized
