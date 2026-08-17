import os
import sys

from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STRIPE_E2E_DISABLED = "STRIPE_E2E_DISABLED"
STRIPE_LIVE_MODE_RESOURCE = "STRIPE_LIVE_MODE_RESOURCE"
STRIPE_PRICE_INVALID = "STRIPE_PRICE_INVALID"
STRIPE_PRICE_DUPLICATE = "STRIPE_PRICE_DUPLICATE"
STRIPE_PRODUCT_INVALID = "STRIPE_PRODUCT_INVALID"


class StripeTestModeValidationError(RuntimeError):

    def __init__(self, reason_code, message):
        self.reason_code = reason_code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ValidatedPrice:
    plan_key: str
    price_id: str
    product_id: str
    currency: str
    interval: str
    livemode: bool
    active: bool


def load_local_test_env():
    load_dotenv(".env.test.local", override=False)


def require_e2e_opt_in():
    if os.environ.get("STRIPE_E2E_ENABLED", "").strip().lower() != "true":
        raise StripeTestModeValidationError(
            STRIPE_E2E_DISABLED,
            "Set STRIPE_E2E_ENABLED=true to allow Stripe Test Mode validation.",
        )


def assert_stripe_test_mode_resource(resource, *, label):
    if bool(_object_value(resource, "livemode")):
        raise StripeTestModeValidationError(
            STRIPE_LIVE_MODE_RESOURCE,
            f"{label} is a live-mode Stripe resource.",
        )


def validate_prices(stripe_module, *, expected_currency=None):
    from app.services.plan_capacity import PLAN_PROFESSIONAL, PLAN_STARTER
    from app.services.stripe_price_mapping import get_stripe_price_id_for_plan

    starter_price_id = get_stripe_price_id_for_plan(PLAN_STARTER)
    professional_price_id = get_stripe_price_id_for_plan(PLAN_PROFESSIONAL)

    if starter_price_id == professional_price_id:
        raise StripeTestModeValidationError(
            STRIPE_PRICE_DUPLICATE,
            "Starter and Professional must use different Stripe Price IDs.",
        )

    return (
        _validate_price(
            stripe_module,
            PLAN_STARTER,
            starter_price_id,
            expected_currency=expected_currency,
        ),
        _validate_price(
            stripe_module,
            PLAN_PROFESSIONAL,
            professional_price_id,
            expected_currency=expected_currency,
        ),
    )


def _validate_price(
    stripe_module,
    plan_key,
    price_id,
    *,
    expected_currency,
):
    price = stripe_module.Price.retrieve(price_id, expand=["product"])
    assert_stripe_test_mode_resource(price, label=f"{plan_key} price")

    if not bool(_object_value(price, "active")):
        raise StripeTestModeValidationError(
            STRIPE_PRICE_INVALID,
            f"{plan_key} price is not active.",
        )

    if _object_value(price, "type") != "recurring":
        raise StripeTestModeValidationError(
            STRIPE_PRICE_INVALID,
            f"{plan_key} price must be recurring.",
        )

    recurring = _object_value(price, "recurring") or {}
    if _object_value(recurring, "interval") != "month":
        raise StripeTestModeValidationError(
            STRIPE_PRICE_INVALID,
            f"{plan_key} price must use a monthly interval.",
        )

    currency = (_object_value(price, "currency") or "").lower()
    if expected_currency and currency != expected_currency.lower():
        raise StripeTestModeValidationError(
            STRIPE_PRICE_INVALID,
            f"{plan_key} price currency does not match expected currency.",
        )

    product = _object_value(price, "product")
    if isinstance(product, str):
        product = stripe_module.Product.retrieve(product)

    assert_stripe_test_mode_resource(product, label=f"{plan_key} product")
    if not bool(_object_value(product, "active")):
        raise StripeTestModeValidationError(
            STRIPE_PRODUCT_INVALID,
            f"{plan_key} product is not active.",
        )

    return ValidatedPrice(
        plan_key=plan_key,
        price_id=price_id,
        product_id=str(_object_value(product, "id")),
        currency=currency,
        interval=_object_value(recurring, "interval"),
        livemode=bool(_object_value(price, "livemode")),
        active=bool(_object_value(price, "active")),
    )


def configure_app_and_stripe():
    from app import create_app
    from app.services.stripe_service import configure_stripe

    app = create_app()
    with app.app_context():
        stripe_module = configure_stripe()
        return app, stripe_module


def print_safe_price_report(validated_prices):
    from app.services.billing_observability import mask_external_id

    for price in validated_prices:
        print(
            "validated price "
            f"plan={price.plan_key} "
            f"price={mask_external_id(price.price_id)} "
            f"product={mask_external_id(price.product_id)} "
            f"currency={price.currency} "
            f"interval={price.interval} "
            f"livemode={price.livemode} "
            f"active={price.active}"
        )


def run_price_validation():
    load_local_test_env()
    require_e2e_opt_in()
    app, stripe_module = configure_app_and_stripe()

    with app.app_context():
        expected_currency = os.environ.get("STRIPE_E2E_EXPECTED_CURRENCY")
        validated_prices = validate_prices(
            stripe_module,
            expected_currency=expected_currency,
        )
        print_safe_price_report(validated_prices)

    return 0


def main():
    try:
        return run_price_validation()
    except StripeTestModeValidationError as exc:
        print(f"{exc.reason_code}: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"STRIPE_E2E_VALIDATION_FAILED: {exc.__class__.__name__}", file=sys.stderr)
        return 1


def _object_value(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)

    return getattr(obj, key, default)


if __name__ == "__main__":
    raise SystemExit(main())
