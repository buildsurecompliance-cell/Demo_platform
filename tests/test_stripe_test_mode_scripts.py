import os
import types
import unittest

from io import StringIO
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.services.plan_capacity import PLAN_PROFESSIONAL, PLAN_STARTER
from scripts.stripe_test_mode_validation import (
    STRIPE_E2E_DISABLED,
    STRIPE_LIVE_MODE_RESOURCE,
    STRIPE_PRICE_DUPLICATE,
    STRIPE_PRICE_INVALID,
    StripeTestModeValidationError,
    assert_stripe_test_mode_resource,
    print_safe_price_report,
    require_e2e_opt_in,
    validate_prices,
)


class FakePriceAPI:
    prices = {}
    calls = []

    @classmethod
    def retrieve(cls, price_id, expand=None):
        cls.calls.append((price_id, expand))
        return cls.prices[price_id]


class FakeProductAPI:
    products = {}
    calls = []

    @classmethod
    def retrieve(cls, product_id):
        cls.calls.append(product_id)
        return cls.products[product_id]


def stripe_module():
    FakePriceAPI.calls = []
    FakeProductAPI.calls = []
    return types.SimpleNamespace(
        Price=FakePriceAPI,
        Product=FakeProductAPI,
    )


def price(
    price_id,
    product_id,
    *,
    livemode=False,
    active=True,
    price_type="recurring",
    interval="month",
    currency="usd",
):
    return {
        "id": price_id,
        "product": product_id,
        "livemode": livemode,
        "active": active,
        "type": price_type,
        "recurring": {"interval": interval},
        "currency": currency,
    }


def product(product_id, *, livemode=False, active=True):
    return {
        "id": product_id,
        "livemode": livemode,
        "active": active,
    }


class StripeTestModeScriptTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config.update(
            BILLING_PROVIDER="stripe",
            STRIPE_SECRET_KEY="sk_test_fake",
            STRIPE_STARTER_PRICE_ID="price_starter_123456",
            STRIPE_PROFESSIONAL_PRICE_ID="price_professional_987654",
        )
        FakePriceAPI.prices = {
            "price_starter_123456": price(
                "price_starter_123456",
                "prod_starter_123456",
            ),
            "price_professional_987654": price(
                "price_professional_987654",
                "prod_professional_987654",
            ),
        }
        FakeProductAPI.products = {
            "prod_starter_123456": product("prod_starter_123456"),
            "prod_professional_987654": product("prod_professional_987654"),
        }

    def test_opt_in_is_required_before_any_stripe_call(self):
        with patch.dict(os.environ, {"STRIPE_E2E_ENABLED": "false"}):
            with self.assertRaises(StripeTestModeValidationError) as context:
                require_e2e_opt_in()

        self.assertEqual(context.exception.reason_code, STRIPE_E2E_DISABLED)
        self.assertEqual(FakePriceAPI.calls, [])

    def test_validates_test_mode_monthly_recurring_prices(self):
        with self.app.app_context():
            validated = validate_prices(
                stripe_module(),
                expected_currency="usd",
            )

        self.assertEqual(len(validated), 2)
        self.assertEqual(validated[0].plan_key, PLAN_STARTER)
        self.assertEqual(validated[1].plan_key, PLAN_PROFESSIONAL)
        self.assertEqual(validated[0].interval, "month")
        self.assertFalse(validated[0].livemode)
        self.assertEqual(
            FakePriceAPI.calls,
            [
                ("price_starter_123456", ["product"]),
                ("price_professional_987654", ["product"]),
            ],
        )

    def test_rejects_live_mode_resource(self):
        with self.assertRaises(StripeTestModeValidationError) as context:
            assert_stripe_test_mode_resource(
                {"livemode": True},
                label="test price",
            )

        self.assertEqual(context.exception.reason_code, STRIPE_LIVE_MODE_RESOURCE)

    def test_rejects_one_time_inactive_or_wrong_interval_prices(self):
        bad_cases = (
            {"price_type": "one_time"},
            {"active": False},
            {"interval": "year"},
            {"currency": "eur"},
        )

        for override in bad_cases:
            with self.subTest(override=override):
                FakePriceAPI.prices["price_starter_123456"] = price(
                    "price_starter_123456",
                    "prod_starter_123456",
                    **override,
                )
                with self.app.app_context():
                    with self.assertRaises(StripeTestModeValidationError) as context:
                        validate_prices(
                            stripe_module(),
                            expected_currency="usd",
                        )
                self.assertEqual(
                    context.exception.reason_code,
                    STRIPE_PRICE_INVALID,
                )

    def test_rejects_same_price_for_two_self_service_plans(self):
        self.app.config["STRIPE_PROFESSIONAL_PRICE_ID"] = "price_starter_123456"

        with self.app.app_context():
            with self.assertRaises(StripeTestModeValidationError) as context:
                validate_prices(stripe_module())

        self.assertEqual(context.exception.reason_code, STRIPE_PRICE_DUPLICATE)

    def test_safe_report_masks_price_and_product_ids(self):
        with self.app.app_context():
            validated = validate_prices(stripe_module())

        output = StringIO()
        with patch("sys.stdout", output):
            print_safe_price_report(validated)

        rendered = output.getvalue()
        self.assertIn("price_****3456", rendered)
        self.assertIn("prod_****3456", rendered)
        self.assertNotIn("price_starter_123456", rendered)
        self.assertNotIn("prod_starter_123456", rendered)


if __name__ == "__main__":
    unittest.main()
