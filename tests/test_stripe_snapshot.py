import os
import unittest

from datetime import datetime, timezone

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.services.billing_observability import mask_external_id
from app.services.stripe_service import (
    get_stripe_configuration_status,
)
from app.services.stripe_subscription_sync import (
    STRIPE_EVENT_CONFLICT,
    STRIPE_EVENT_DUPLICATE,
    STRIPE_EVENT_OUT_OF_ORDER,
    snapshot_from_stripe_subscription,
    should_apply_stripe_event,
)


class DummySubscription:
    stripe_event_created_at = None
    stripe_event_id = None


class StripeSnapshotTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config.update(
            BILLING_PROVIDER="stripe",
            STRIPE_SECRET_KEY="sk_test_fake",
            STRIPE_WEBHOOK_SECRET="whsec_fake",
            STRIPE_STARTER_PRICE_ID="price_starter",
            STRIPE_PROFESSIONAL_PRICE_ID="price_professional",
            BILLING_SUCCESS_URL="https://example.test/success",
            BILLING_CANCEL_URL="https://example.test/cancel",
            BILLING_PORTAL_RETURN_URL="https://example.test/portal",
        )

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def stripe_subscription(self, **overrides):
        base = {
            "id": "sub_snapshot",
            "customer": "cus_snapshot",
            "status": "active",
            "current_period_start": 1710000000,
            "current_period_end": 1712600000,
            "cancel_at": None,
            "metadata": {"organization_id": "42"},
            "items": {"data": [{"price": {"id": "price_professional"}}]},
        }
        base.update(overrides)
        return base

    def test_snapshot_converts_fields_without_mutating_models_or_committing(self):
        with self.app.app_context():
            snapshot = snapshot_from_stripe_subscription(
                self.stripe_subscription(),
                event_id="evt_snapshot",
                event_created_at=1710000100,
            )

            self.assertEqual(snapshot.billing_subscription_id, "sub_snapshot")
            self.assertEqual(snapshot.billing_customer_id, "cus_snapshot")
            self.assertEqual(snapshot.billing_price_id, "price_professional")
            self.assertEqual(snapshot.status, "active")
            self.assertEqual(snapshot.organization_id, 42)
            self.assertIs(snapshot.current_period_end.tzinfo, timezone.utc)
            self.assertIsNone(snapshot.cancel_at)
            self.assertFalse(db.session.new)
            self.assertFalse(db.session.dirty)

    def test_snapshot_converts_basil_cancel_at(self):
        with self.app.app_context():
            snapshot = snapshot_from_stripe_subscription(
                self.stripe_subscription(
                    cancel_at_period_end=False,
                    cancel_at=1785974400,
                    canceled_at=1785283200,
                )
            )

            self.assertFalse(snapshot.cancel_at_period_end)
            self.assertEqual(
                snapshot.cancel_at,
                datetime.fromtimestamp(1785974400, tz=timezone.utc),
            )
            self.assertEqual(
                snapshot.canceled_at,
                datetime.fromtimestamp(1785283200, tz=timezone.utc),
            )

    def test_snapshot_reads_billing_period_from_subscription_item(self):
        with self.app.app_context():
            snapshot = snapshot_from_stripe_subscription(
                self.stripe_subscription(
                    current_period_start=None,
                    current_period_end=None,
                    items={
                        "data": [
                            {
                                "price": {"id": "price_professional"},
                                "current_period_start": 1710001000,
                                "current_period_end": 1712601000,
                            }
                        ]
                    },
                )
            )

            self.assertEqual(
                snapshot.current_period_start,
                datetime.fromtimestamp(1710001000, tz=timezone.utc),
            )
            self.assertEqual(
                snapshot.current_period_end,
                datetime.fromtimestamp(1712601000, tz=timezone.utc),
            )
            self.assertEqual(snapshot.billing_price_id, "price_professional")

    def test_snapshot_missing_billing_period_remains_none(self):
        with self.app.app_context():
            snapshot = snapshot_from_stripe_subscription(
                self.stripe_subscription(
                    current_period_start=None,
                    current_period_end=None,
                    items={"data": [{"price": {"id": "price_professional"}}]},
                )
            )

            self.assertIsNone(snapshot.current_period_start)
            self.assertIsNone(snapshot.current_period_end)

    def test_snapshot_rejects_bad_items_price_and_status(self):
        with self.app.app_context():
            bad_cases = (
                {"items": {"data": []}},
                {"items": {"data": [{"price": {}}, {"price": {}}]}},
                {"items": {"data": [{"price": {"id": ""}}]}},
                {"status": "mystery"},
            )
            for override in bad_cases:
                with self.subTest(override=override):
                    with self.assertRaises(Exception):
                        snapshot_from_stripe_subscription(
                            self.stripe_subscription(**override)
                        )

    def test_event_application_decision_is_conservative(self):
        from datetime import datetime

        subscription = DummySubscription()
        subscription.stripe_event_created_at = datetime.fromtimestamp(
            1710000100,
            tz=timezone.utc,
        )
        subscription.stripe_event_id = "evt_current"

        older = should_apply_stripe_event(
            subscription,
            event_id="evt_old",
            event_created_at=datetime.fromtimestamp(1710000000, tz=timezone.utc),
        )
        duplicate = should_apply_stripe_event(
            subscription,
            event_id="evt_current",
            event_created_at=subscription.stripe_event_created_at,
        )
        conflict = should_apply_stripe_event(
            subscription,
            event_id="evt_conflict",
            event_created_at=subscription.stripe_event_created_at,
        )

        self.assertEqual(older.reason_code, STRIPE_EVENT_OUT_OF_ORDER)
        self.assertEqual(duplicate.reason_code, STRIPE_EVENT_DUPLICATE)
        self.assertEqual(conflict.reason_code, STRIPE_EVENT_CONFLICT)

    def test_configuration_status_and_masking_do_not_expose_values(self):
        with self.app.app_context():
            status = get_stripe_configuration_status()
            self.assertTrue(status["enabled"])
            self.assertTrue(status["ready"])
            self.assertEqual(status["missing"], [])

            self.app.config["STRIPE_WEBHOOK_SECRET"] = None
            degraded = get_stripe_configuration_status()
            self.assertFalse(degraded["ready"])
            self.assertIn("missing_webhook_secret", degraded["missing"])

        self.assertEqual(mask_external_id("cus_123456789"), "cus_****6789")
        self.assertEqual(mask_external_id("short"), "****")


if __name__ == "__main__":
    unittest.main()
