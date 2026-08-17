import os
import unittest

from datetime import timedelta
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import BillingEvent, EVENT_FAILED, EVENT_PROCESSED, EVENT_PROCESSING, User
from app.services.billing_observability import utc_now
from app.services.billing_event_service import create_billing_event
from app.services.organizations import create_default_organization_for_user
from app.services.plan_capacity import PLAN_PROFESSIONAL
from app.services.subscription_service import STATUS_ACTIVE


class StripeRetryTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=True,
            BILLING_PROVIDER="stripe",
            STRIPE_SECRET_KEY="sk_test_fake",
            STRIPE_WEBHOOK_SECRET="whsec_fake",
            STRIPE_STARTER_PRICE_ID="price_starter",
            STRIPE_PROFESSIONAL_PRICE_ID="price_professional",
            BILLING_EVENT_PROCESSING_TIMEOUT_SECONDS=300,
        )
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()
            user = User(email="owner@example.com", paid=False)
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()
            organization = create_default_organization_for_user(user)
            db.session.commit()
            self.organization_id = organization.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def event(self, event_id="evt_retry"):
        return {
            "id": event_id,
            "type": "customer.subscription.updated",
            "created": 1710000100,
            "data": {
                "object": {
                    "id": "sub_retry",
                    "customer": "cus_retry",
                    "status": STATUS_ACTIVE,
                    "metadata": {"organization_id": str(self.organization_id)},
                    "items": {"data": [{"price": {"id": "price_professional"}}]},
                }
            },
        }

    def post_event(self, event):
        with patch(
            "app.routes.billing.construct_webhook_event",
            return_value=event,
        ):
            return self.client.post(
                "/billing/webhook/stripe",
                data=b"{}",
                headers={"Stripe-Signature": "sig_test"},
            )

    def test_failed_event_can_retry_and_does_not_duplicate_billing_event(self):
        bad = self.event()
        bad["data"]["object"]["items"]["data"][0]["price"]["id"] = "unknown"
        self.assertEqual(self.post_event(bad).status_code, 500)

        good = self.event()
        self.assertEqual(self.post_event(good).status_code, 200)

        with self.app.app_context():
            events = BillingEvent.query.filter_by(
                external_event_id="evt_retry",
            ).all()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].status, EVENT_PROCESSED)
            self.assertEqual(events[0].attempt_count, 2)
            self.assertIsNotNone(events[0].last_attempt_at)

    def test_processed_event_is_not_reprocessed(self):
        self.assertEqual(self.post_event(self.event("evt_once")).status_code, 200)
        self.assertEqual(self.post_event(self.event("evt_once")).status_code, 200)

        with self.app.app_context():
            event = BillingEvent.query.filter_by(
                external_event_id="evt_once",
            ).one()
            self.assertEqual(event.status, EVENT_PROCESSED)
            self.assertEqual(event.attempt_count, 1)

    def test_processing_recent_waits_and_processing_expired_retries(self):
        with self.app.app_context():
            event = create_billing_event(
                provider="stripe",
                external_event_id="evt_processing",
                event_type="customer.subscription.updated",
            )
            event.status = EVENT_PROCESSING
            event.attempt_count = 1
            event.last_attempt_at = utc_now()
            db.session.commit()

        recent = self.post_event(self.event("evt_processing"))
        self.assertEqual(recent.status_code, 200)

        with self.app.app_context():
            event = BillingEvent.query.filter_by(
                external_event_id="evt_processing",
            ).one()
            self.assertEqual(event.status, EVENT_PROCESSING)
            self.assertEqual(event.attempt_count, 1)
            event.last_attempt_at = utc_now() - timedelta(minutes=10)
            db.session.commit()

        retried = self.post_event(self.event("evt_processing"))
        self.assertEqual(retried.status_code, 200)

        with self.app.app_context():
            event = BillingEvent.query.filter_by(
                external_event_id="evt_processing",
            ).one()
            self.assertEqual(event.status, EVENT_PROCESSED)
            self.assertEqual(event.attempt_count, 2)

    def test_failure_after_mutation_rolls_back_and_marks_failed(self):
        event = self.event("evt_rollback")

        with patch(
            "app.routes.billing.process_stripe_event",
            side_effect=RuntimeError("transient"),
        ), patch(
            "app.routes.billing.construct_webhook_event",
            return_value=event,
        ):
            response = self.client.post(
                "/billing/webhook/stripe",
                data=b"{}",
                headers={"Stripe-Signature": "sig_test"},
            )

        self.assertEqual(response.status_code, 500)

        with self.app.app_context():
            billing_event = BillingEvent.query.filter_by(
                external_event_id="evt_rollback",
            ).one()
            self.assertEqual(billing_event.status, EVENT_FAILED)
            self.assertEqual(billing_event.error_message, "RuntimeError")


if __name__ == "__main__":
    unittest.main()
