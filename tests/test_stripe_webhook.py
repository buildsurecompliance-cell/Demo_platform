import os
import unittest

from datetime import datetime, timezone
from unittest.mock import patch

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
    Organization,
    Subscription,
    User,
)
from app.services.organizations import create_default_organization_for_user
from app.services.plan_capacity import PLAN_PROFESSIONAL, PLAN_STARTER
from app.services.stripe_service import (
    STRIPE_WEBHOOK_SIGNATURE_INVALID,
    StripeWebhookError,
)
from app.services.subscription_service import (
    PROVIDER_STRIPE,
    STATUS_ACTIVE,
    STATUS_CANCELED,
    STATUS_INACTIVE,
    STATUS_PAST_DUE,
)


def utc_database_datetime(timestamp):
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)


class StripeWebhookRouteTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=True,
            PROPAGATE_EXCEPTIONS=False,
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
            organization.plan_key = PLAN_STARTER
            organization.subscription.status = STATUS_INACTIVE
            db.session.commit()

            self.organization_id = organization.id
            self.subscription_id = organization.subscription.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

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

    def subscription_event(
        self,
        *,
        event_id="evt_subscription",
        event_type="customer.subscription.updated",
        price_id="price_professional",
        status="active",
        customer="cus_123",
        subscription="sub_123",
        created=1710000100,
        current_period_start=1710000000,
        current_period_end=1712600000,
        item_current_period_start=None,
        item_current_period_end=None,
        cancel_at=None,
        cancel_at_period_end=False,
        canceled_at=None,
    ):
        item = {
            "price": {
                "id": price_id,
            }
        }
        if item_current_period_start is not None:
            item["current_period_start"] = item_current_period_start
        if item_current_period_end is not None:
            item["current_period_end"] = item_current_period_end

        return {
            "id": event_id,
            "type": event_type,
            "created": created,
            "data": {
                "object": {
                    "id": subscription,
                    "customer": customer,
                    "status": status,
                    "current_period_start": current_period_start,
                    "current_period_end": current_period_end,
                    "cancel_at": cancel_at,
                    "cancel_at_period_end": cancel_at_period_end,
                    "canceled_at": canceled_at,
                    "metadata": {
                        "organization_id": str(self.organization_id),
                    },
                    "items": {
                        "data": [item]
                    },
                }
            },
        }

    def test_invalid_signature_returns_400(self):
        with patch(
            "app.routes.billing.construct_webhook_event",
            side_effect=StripeWebhookError(
                STRIPE_WEBHOOK_SIGNATURE_INVALID,
                "invalid",
            ),
        ):
            response = self.client.post(
                "/billing/webhook/stripe",
                data=b"{}",
                headers={"Stripe-Signature": "bad"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "invalid_webhook"})

    def test_unknown_event_is_idempotently_ignored_without_login_or_csrf(self):
        event = {
            "id": "evt_unknown",
            "type": "account.updated",
            "data": {"object": {"id": "acct_123"}},
        }

        first = self.post_event(event)
        second = self.post_event(event)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)

        with self.app.app_context():
            events = BillingEvent.query.filter_by(
                external_event_id="evt_unknown",
            ).all()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].status, EVENT_IGNORED)

    def test_checkout_completed_associates_ids_but_does_not_activate(self):
        event = {
            "id": "evt_checkout",
            "type": "checkout.session.completed",
            "created": 1710000100,
            "data": {
                "object": {
                    "mode": "subscription",
                    "customer": "cus_checkout",
                    "subscription": "sub_checkout",
                    "client_reference_id": str(self.organization_id),
                    "metadata": {
                        "organization_id": str(self.organization_id),
                        "plan_key": PLAN_PROFESSIONAL,
                    },
                }
            },
        }

        response = self.post_event(event)

        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            subscription = organization.subscription
            self.assertEqual(subscription.provider, PROVIDER_STRIPE)
            self.assertEqual(subscription.billing_customer_id, "cus_checkout")
            self.assertEqual(subscription.billing_subscription_id, "sub_checkout")
            self.assertEqual(subscription.status, STATUS_INACTIVE)
            self.assertEqual(organization.plan_key, PLAN_STARTER)

    def test_subscription_updated_uses_price_mapping_as_plan_source(self):
        response = self.post_event(self.subscription_event())

        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            subscription = organization.subscription
            self.assertEqual(organization.plan_key, PLAN_PROFESSIONAL)
            self.assertEqual(subscription.status, STATUS_ACTIVE)
            self.assertEqual(subscription.provider, PROVIDER_STRIPE)
            self.assertEqual(subscription.billing_price_id, "price_professional")
            self.assertEqual(subscription.billing_customer_id, "cus_123")
            self.assertEqual(subscription.billing_subscription_id, "sub_123")
            self.assertIsNotNone(subscription.current_period_end)
            self.assertEqual(
                BillingEvent.query.filter_by(
                    external_event_id="evt_subscription",
                ).one().status,
                EVENT_PROCESSED,
            )

    def test_subscription_item_periods_are_persisted_from_webhook_snapshot(self):
        response = self.post_event(
            self.subscription_event(
                event_id="evt_item_period",
                current_period_start=None,
                current_period_end=None,
                item_current_period_start=1710002000,
                item_current_period_end=1712602000,
            )
        )

        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            subscription = organization.subscription
            self.assertEqual(organization.plan_key, PLAN_PROFESSIONAL)
            self.assertEqual(subscription.billing_price_id, "price_professional")
            self.assertEqual(
                subscription.current_period_start,
                utc_database_datetime(1710002000),
            )
            self.assertEqual(
                subscription.current_period_end,
                utc_database_datetime(1712602000),
            )

    def test_webhook_persists_basil_scheduled_cancellation(self):
        response = self.post_event(
            self.subscription_event(
                event_id="evt_basil_cancel",
                current_period_start=None,
                current_period_end=None,
                item_current_period_start=1783296000,
                item_current_period_end=1785974400,
                cancel_at=1785974400,
                cancel_at_period_end=False,
                canceled_at=1785283200,
            )
        )

        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            subscription = organization.subscription
            self.assertEqual(organization.plan_key, PLAN_PROFESSIONAL)
            self.assertEqual(subscription.status, STATUS_ACTIVE)
            self.assertFalse(subscription.cancel_at_period_end)
            self.assertEqual(
                subscription.cancel_at,
                utc_database_datetime(1785974400),
            )
            self.assertEqual(
                subscription.current_period_end,
                utc_database_datetime(1785974400),
            )

    def test_duplicate_subscription_event_with_item_period_is_idempotent(self):
        event = self.subscription_event(
            event_id="evt_item_period_duplicate",
            current_period_start=None,
            current_period_end=None,
            item_current_period_start=1710003000,
            item_current_period_end=1712603000,
        )

        first = self.post_event(event)
        second = self.post_event(event)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            subscription = organization.subscription
            event_row = BillingEvent.query.filter_by(
                external_event_id="evt_item_period_duplicate",
            ).one()
            self.assertEqual(event_row.status, EVENT_PROCESSED)
            self.assertEqual(event_row.attempt_count, 1)
            self.assertEqual(
                subscription.current_period_end,
                utc_database_datetime(1712603000),
            )

    def test_unknown_price_fails_without_mutating_subscription(self):
        response = self.post_event(
            self.subscription_event(
                event_id="evt_bad_price",
                price_id="price_unknown",
            )
        )

        self.assertEqual(response.status_code, 500)

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            subscription = organization.subscription
            self.assertEqual(organization.plan_key, PLAN_STARTER)
            self.assertEqual(subscription.status, STATUS_INACTIVE)
            self.assertIsNone(subscription.billing_price_id)
            event = BillingEvent.query.filter_by(
                external_event_id="evt_bad_price",
            ).one()
            self.assertEqual(event.status, EVENT_FAILED)

    def test_subscription_deleted_cancels_but_preserves_plan(self):
        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            organization.plan_key = PLAN_PROFESSIONAL
            organization.subscription.provider = PROVIDER_STRIPE
            organization.subscription.billing_customer_id = "cus_123"
            organization.subscription.billing_subscription_id = "sub_123"
            organization.subscription.status = STATUS_ACTIVE
            db.session.commit()

        event = self.subscription_event(
            event_id="evt_deleted",
            event_type="customer.subscription.deleted",
            price_id="price_professional",
        )
        response = self.post_event(event)

        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            self.assertEqual(organization.plan_key, PLAN_PROFESSIONAL)
            self.assertEqual(organization.subscription.status, STATUS_CANCELED)

    def test_invoice_payment_failed_marks_known_subscription_past_due(self):
        with self.app.app_context():
            subscription = db.session.get(Subscription, self.subscription_id)
            subscription.provider = PROVIDER_STRIPE
            subscription.billing_subscription_id = "sub_invoice"
            subscription.status = STATUS_ACTIVE
            db.session.commit()

        event = {
            "id": "evt_invoice_failed",
            "type": "invoice.payment_failed",
            "created": 1710000100,
            "data": {
                "object": {
                    "subscription": "sub_invoice",
                }
            },
        }
        with patch(
            "app.services.stripe_webhook_service.retrieve_subscription",
            return_value=self.subscription_event(
                event_id="evt_remote",
                subscription="sub_invoice",
                status="past_due",
            )["data"]["object"],
        ):
            response = self.post_event(event)

        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            subscription = db.session.get(Subscription, self.subscription_id)
            self.assertEqual(subscription.status, STATUS_PAST_DUE)

    def test_invoice_payment_succeeded_recovers_known_past_due_subscription(self):
        with self.app.app_context():
            subscription = db.session.get(Subscription, self.subscription_id)
            subscription.provider = PROVIDER_STRIPE
            subscription.billing_subscription_id = "sub_invoice"
            subscription.status = STATUS_PAST_DUE
            db.session.commit()

        event = {
            "id": "evt_invoice_succeeded",
            "type": "invoice.payment_succeeded",
            "created": 1710000100,
            "data": {
                "object": {
                    "subscription": "sub_invoice",
                }
            },
        }
        with patch(
            "app.services.stripe_webhook_service.retrieve_subscription",
            return_value=self.subscription_event(
                event_id="evt_remote",
                subscription="sub_invoice",
                status="active",
            )["data"]["object"],
        ):
            response = self.post_event(event)

        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            subscription = db.session.get(Subscription, self.subscription_id)
            organization = db.session.get(Organization, self.organization_id)
            self.assertEqual(subscription.status, STATUS_ACTIVE)
            self.assertEqual(organization.plan_key, PLAN_PROFESSIONAL)


if __name__ == "__main__":
    unittest.main()
