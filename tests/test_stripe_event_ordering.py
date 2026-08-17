import os
import unittest

from datetime import datetime, timezone
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import BillingEvent, Organization, User
from app.services.organizations import create_default_organization_for_user
from app.services.plan_capacity import PLAN_PROFESSIONAL, PLAN_STARTER
from app.services.subscription_service import (
    PROVIDER_STRIPE,
    STATUS_ACTIVE,
    STATUS_CANCELED,
    STATUS_PAST_DUE,
)


def utc_database_datetime(timestamp):
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)


class StripeEventOrderingTest(unittest.TestCase):

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

    def subscription_event(
        self,
        event_id,
        created,
        status,
        price_id="price_professional",
        current_period_start=1710000000,
        current_period_end=None,
        item_current_period_start=None,
        item_current_period_end=None,
        include_top_level_period=True,
        cancel_at=None,
        cancel_at_period_end=False,
        canceled_at=None,
    ):
        item = {"price": {"id": price_id}}
        if item_current_period_start is not None:
            item["current_period_start"] = item_current_period_start
        if item_current_period_end is not None:
            item["current_period_end"] = item_current_period_end

        return {
            "id": event_id,
            "type": "customer.subscription.updated",
            "created": created,
            "data": {
                "object": {
                    "id": "sub_order",
                    "customer": "cus_order",
                    "status": status,
                    "current_period_start": (
                        current_period_start if include_top_level_period else None
                    ),
                    "current_period_end": (
                        (
                            current_period_end
                            if current_period_end is not None
                            else created + 1000
                        )
                        if include_top_level_period
                        else None
                    ),
                    "cancel_at": cancel_at,
                    "cancel_at_period_end": cancel_at_period_end,
                    "canceled_at": canceled_at,
                    "metadata": {"organization_id": str(self.organization_id)},
                    "items": {"data": [item]},
                }
            },
        }

    def deleted_event(self, event_id, created):
        return {
            "id": event_id,
            "type": "customer.subscription.deleted",
            "created": created,
            "data": {
                "object": {
                    "id": "sub_order",
                    "customer": "cus_order",
                    "status": "canceled",
                    "metadata": {"organization_id": str(self.organization_id)},
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

    def test_newer_event_applies_and_older_arriving_later_is_ignored(self):
        newer = self.post_event(
            self.subscription_event(
                "evt_newer",
                1710000500,
                STATUS_ACTIVE,
            )
        )
        older = self.post_event(
            self.subscription_event(
                "evt_older",
                1710000100,
                STATUS_PAST_DUE,
                price_id="price_starter",
            )
        )

        self.assertEqual(newer.status_code, 200)
        self.assertEqual(older.status_code, 200)

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            subscription = organization.subscription
            self.assertEqual(subscription.status, STATUS_ACTIVE)
            self.assertEqual(organization.plan_key, PLAN_PROFESSIONAL)
            self.assertEqual(subscription.stripe_event_id, "evt_newer")
            event = BillingEvent.query.filter_by(
                external_event_id="evt_older",
            ).one()
            self.assertEqual(event.status, "ignored")
            self.assertEqual(event.error_message, "STRIPE_EVENT_OUT_OF_ORDER")

    def test_old_item_period_event_cannot_overwrite_newer_period_data(self):
        newer_period_end = 1712606000
        older_period_end = 1712601000

        newer = self.post_event(
            self.subscription_event(
                "evt_item_period_newer",
                1710000600,
                STATUS_ACTIVE,
                include_top_level_period=False,
                item_current_period_start=1710006000,
                item_current_period_end=newer_period_end,
            )
        )
        older = self.post_event(
            self.subscription_event(
                "evt_item_period_older",
                1710000100,
                STATUS_PAST_DUE,
                price_id="price_starter",
                include_top_level_period=False,
                item_current_period_start=1710001000,
                item_current_period_end=older_period_end,
            )
        )

        self.assertEqual(newer.status_code, 200)
        self.assertEqual(older.status_code, 200)

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            subscription = organization.subscription
            self.assertEqual(subscription.status, STATUS_ACTIVE)
            self.assertEqual(organization.plan_key, PLAN_PROFESSIONAL)
            self.assertEqual(subscription.stripe_event_id, "evt_item_period_newer")
            self.assertEqual(
                subscription.current_period_end,
                utc_database_datetime(newer_period_end),
            )
            event = BillingEvent.query.filter_by(
                external_event_id="evt_item_period_older",
            ).one()
            self.assertEqual(event.status, "ignored")
            self.assertEqual(event.error_message, "STRIPE_EVENT_OUT_OF_ORDER")

    def test_old_event_cannot_remove_newer_scheduled_cancellation(self):
        cancel_at = 1785974400

        newer = self.post_event(
            self.subscription_event(
                "evt_cancel_newer",
                1785283200,
                STATUS_ACTIVE,
                include_top_level_period=False,
                item_current_period_start=1783296000,
                item_current_period_end=cancel_at,
                cancel_at=cancel_at,
                cancel_at_period_end=False,
                canceled_at=1785283200,
            )
        )
        older = self.post_event(
            self.subscription_event(
                "evt_cancel_older",
                1785196800,
                STATUS_ACTIVE,
                include_top_level_period=False,
                item_current_period_start=1783296000,
                item_current_period_end=cancel_at,
                cancel_at=None,
                cancel_at_period_end=False,
                canceled_at=None,
            )
        )

        self.assertEqual(newer.status_code, 200)
        self.assertEqual(older.status_code, 200)

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            subscription = organization.subscription
            self.assertEqual(subscription.status, STATUS_ACTIVE)
            self.assertEqual(organization.plan_key, PLAN_PROFESSIONAL)
            self.assertEqual(subscription.stripe_event_id, "evt_cancel_newer")
            self.assertEqual(
                subscription.cancel_at,
                utc_database_datetime(cancel_at),
            )
            event = BillingEvent.query.filter_by(
                external_event_id="evt_cancel_older",
            ).one()
            self.assertEqual(event.status, "ignored")
            self.assertEqual(event.error_message, "STRIPE_EVENT_OUT_OF_ORDER")

    def test_deleted_newer_cannot_be_overwritten_by_old_active_update(self):
        self.post_event(
            self.subscription_event(
                "evt_active",
                1710000100,
                STATUS_ACTIVE,
            )
        )
        deleted = self.post_event(
            self.deleted_event("evt_deleted_newer", 1710000600)
        )
        old_active = self.post_event(
            self.subscription_event(
                "evt_active_old",
                1710000200,
                STATUS_ACTIVE,
            )
        )

        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(old_active.status_code, 200)

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            self.assertEqual(organization.subscription.status, STATUS_CANCELED)
            self.assertEqual(organization.plan_key, PLAN_PROFESSIONAL)
            self.assertEqual(
                organization.subscription.stripe_event_id,
                "evt_deleted_newer",
            )

    def test_same_timestamp_different_event_is_ignored_as_conflict(self):
        self.post_event(
            self.subscription_event("evt_same_a", 1710000100, STATUS_ACTIVE)
        )
        response = self.post_event(
            self.subscription_event(
                "evt_same_b",
                1710000100,
                STATUS_PAST_DUE,
            )
        )

        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            self.assertEqual(organization.subscription.status, STATUS_ACTIVE)
            event = BillingEvent.query.filter_by(
                external_event_id="evt_same_b",
            ).one()
            self.assertEqual(event.status, "ignored")
            self.assertEqual(event.error_message, "STRIPE_EVENT_CONFLICT")

    def test_missing_or_invalid_event_created_fails_without_mutation(self):
        missing = self.subscription_event("evt_missing_created", 1710000100, STATUS_ACTIVE)
        missing.pop("created")
        invalid = self.subscription_event("evt_invalid_created", 1710000100, STATUS_ACTIVE)
        invalid["created"] = "not-a-timestamp"

        self.assertEqual(self.post_event(missing).status_code, 500)
        self.assertEqual(self.post_event(invalid).status_code, 500)

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            self.assertEqual(organization.plan_key, PLAN_STARTER)
            self.assertNotEqual(organization.subscription.provider, PROVIDER_STRIPE)
            failed = BillingEvent.query.filter_by(
                external_event_id="evt_missing_created",
            ).one()
            self.assertEqual(failed.status, "failed")
            self.assertEqual(failed.attempt_count, 1)


if __name__ == "__main__":
    unittest.main()
