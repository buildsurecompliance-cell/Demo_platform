import os
import unittest

from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import (
    BillingEvent,
    EVENT_FAILED,
    EVENT_IGNORED,
    EVENT_PROCESSED,
    EVENT_PROCESSING,
    EVENT_RECEIVED,
    Organization,
    User,
)
from app.services.billing_event_service import (
    BillingEventValidationError,
    create_billing_event,
    get_billing_event,
    is_event_processed,
    mark_event_failed,
    mark_event_ignored,
    mark_event_processed,
    mark_event_processing,
)
from app.services.organizations import create_default_organization_for_user


class BillingEventServiceTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestingConfig)

        with self.app.app_context():
            db.create_all()
            user = User(email="owner@example.com", paid=False)
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()
            organization = create_default_organization_for_user(user)
            db.session.commit()

            self.organization_id = organization.id
            self.subscription_id = organization.subscription.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def test_create_event_records_idempotency_without_payload(self):
        with self.app.app_context():
            organization = db.session.get(
                Organization,
                self.organization_id,
            )
            subscription = organization.subscription

            event = create_billing_event(
                provider="stripe",
                external_event_id="evt_123",
                event_type="customer.subscription.updated",
                organization=organization,
                subscription=subscription,
            )
            db.session.commit()

            self.assertEqual(event.provider, "stripe")
            self.assertEqual(event.external_event_id, "evt_123")
            self.assertEqual(event.event_type, "customer.subscription.updated")
            self.assertEqual(event.status, EVENT_RECEIVED)
            self.assertEqual(event.organization_id, organization.id)
            self.assertEqual(event.subscription_id, subscription.id)
            self.assertFalse(hasattr(event, "payload"))
            self.assertFalse(hasattr(event, "raw_payload"))
            self.assertIs(
                get_billing_event("stripe", "evt_123"),
                event,
            )

    def test_unique_constraint_is_provider_scoped(self):
        with self.app.app_context():
            create_billing_event(
                provider="stripe",
                external_event_id="evt_same",
                event_type="first",
            )
            create_billing_event(
                provider="internal",
                external_event_id="evt_same",
                event_type="first",
            )
            db.session.commit()

            with self.assertRaises(IntegrityError):
                create_billing_event(
                    provider="stripe",
                    external_event_id="evt_same",
                    event_type="duplicate",
                )
            db.session.rollback()

            self.assertEqual(BillingEvent.query.count(), 2)

    def test_validation_rejects_invalid_provider_ids_and_type(self):
        with self.app.app_context():
            invalid_cases = (
                ("unknown", "evt_1", "event"),
                ("stripe", "", "event"),
                ("stripe", "   ", "event"),
                ("stripe", "x" * 256, "event"),
                ("stripe", "evt_2", ""),
            )

            for provider, event_id, event_type in invalid_cases:
                with self.subTest(provider=provider, event_id=event_id):
                    with self.assertRaises(BillingEventValidationError):
                        create_billing_event(
                            provider=provider,
                            external_event_id=event_id,
                            event_type=event_type,
                        )

            self.assertEqual(BillingEvent.query.count(), 0)

    def test_status_transitions_do_not_commit_or_change_subscription(self):
        with self.app.app_context():
            organization = db.session.get(
                Organization,
                self.organization_id,
            )
            original_plan_key = organization.plan_key
            original_subscription_status = organization.subscription.status
            event = create_billing_event(
                provider="stripe",
                external_event_id="evt_status",
                event_type="customer.subscription.updated",
                organization=organization,
                subscription=organization.subscription,
            )
            db.session.flush()

            mark_event_processing(event)
            self.assertEqual(event.status, EVENT_PROCESSING)
            mark_event_processed(event)
            self.assertEqual(event.status, EVENT_PROCESSED)
            self.assertIsNotNone(event.processed_at)
            self.assertTrue(is_event_processed("stripe", "evt_status"))

            self.assertEqual(organization.plan_key, original_plan_key)
            self.assertEqual(
                organization.subscription.status,
                original_subscription_status,
            )

    def test_failed_and_ignored_events_are_safe_and_truncate_errors(self):
        with self.app.app_context():
            event = create_billing_event(
                provider="stripe",
                external_event_id="evt_failed",
                event_type="event",
            )
            db.session.flush()
            processed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

            mark_event_failed(
                event,
                error_message="x" * 600,
                processed_at=processed_at,
            )
            self.assertEqual(event.status, EVENT_FAILED)
            self.assertEqual(event.processed_at, processed_at)
            self.assertEqual(len(event.error_message), 500)
            self.assertFalse(is_event_processed("stripe", "evt_failed"))

            mark_event_ignored(event)
            self.assertEqual(event.status, EVENT_IGNORED)

    def test_event_can_exist_without_organization_or_subscription(self):
        with self.app.app_context():
            event = create_billing_event(
                provider="stripe",
                external_event_id="evt_no_org",
                event_type="account.updated",
            )
            db.session.commit()

            self.assertIsNone(event.organization_id)
            self.assertIsNone(event.subscription_id)

    def test_invalid_status_transition_is_rejected(self):
        with self.app.app_context():
            event = create_billing_event(
                provider="stripe",
                external_event_id="evt_bad_status",
                event_type="event",
            )

            with self.assertRaises(BillingEventValidationError):
                __import__(
                    "app.services.billing_event_service",
                    fromlist=["_set_event_status"],
                )._set_event_status(event, "done")


if __name__ == "__main__":
    unittest.main()
