import os
import unittest

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
    STATUS_ACTIVE,
    STATUS_PAST_DUE,
)


class StripeInvoiceWebhookTest(unittest.TestCase):

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

    def invoice_event(self, event_id, event_type, created=1710000100):
        return {
            "id": event_id,
            "type": event_type,
            "created": created,
            "data": {
                "object": {
                    "id": "in_invoice",
                    "subscription": "sub_invoice",
                    "amount_paid": 99999999,
                    "description": "Professional plan payment",
                    "metadata": {"plan_key": PLAN_STARTER},
                }
            },
        }

    def remote_subscription(self, *, status, price_id, subscription="sub_invoice"):
        return {
            "id": subscription,
            "customer": "cus_invoice",
            "status": status,
            "current_period_start": 1710000000,
            "current_period_end": 1712600000,
            "metadata": {"organization_id": str(self.organization_id)},
            "items": {"data": [{"price": {"id": price_id}}]},
        }

    def post_event(self, event, remote_subscription=None):
        patches = [
            patch("app.routes.billing.construct_webhook_event", return_value=event),
        ]
        if remote_subscription is not None:
            patches.append(
                patch(
                    "app.services.stripe_webhook_service.retrieve_subscription",
                    return_value=remote_subscription,
                )
            )

        with patches[0]:
            if len(patches) == 2:
                with patches[1]:
                    return self.client.post(
                        "/billing/webhook/stripe",
                        data=b"{}",
                        headers={"Stripe-Signature": "sig_test"},
                    )
            return self.client.post(
                "/billing/webhook/stripe",
                data=b"{}",
                headers={"Stripe-Signature": "sig_test"},
            )

    def test_payment_succeeded_uses_remote_subscription_snapshot_only(self):
        response = self.post_event(
            self.invoice_event("evt_invoice_success", "invoice.payment_succeeded"),
            self.remote_subscription(
                status=STATUS_ACTIVE,
                price_id="price_professional",
            ),
        )

        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            self.assertEqual(organization.subscription.status, STATUS_ACTIVE)
            self.assertEqual(organization.plan_key, PLAN_PROFESSIONAL)
            self.assertEqual(
                organization.subscription.billing_price_id,
                "price_professional",
            )

    def test_payment_failed_uses_remote_status_and_preserves_plan_mapping(self):
        response = self.post_event(
            self.invoice_event("evt_invoice_failed", "invoice.payment_failed"),
            self.remote_subscription(
                status=STATUS_PAST_DUE,
                price_id="price_professional",
            ),
        )

        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            self.assertEqual(organization.subscription.status, STATUS_PAST_DUE)
            self.assertEqual(organization.plan_key, PLAN_PROFESSIONAL)

    def test_old_invoice_event_does_not_overwrite_newer_subscription_snapshot(self):
        newer = {
            "id": "evt_newer_subscription",
            "type": "customer.subscription.updated",
            "created": 1710000500,
            "data": {
                "object": self.remote_subscription(
                    status=STATUS_ACTIVE,
                    price_id="price_professional",
                )
            },
        }
        old_invoice = self.invoice_event(
            "evt_old_invoice",
            "invoice.payment_failed",
            created=1710000100,
        )

        self.assertEqual(self.post_event(newer).status_code, 200)
        self.assertEqual(
            self.post_event(
                old_invoice,
                self.remote_subscription(
                    status=STATUS_PAST_DUE,
                    price_id="price_starter",
                ),
            ).status_code,
            200,
        )

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            self.assertEqual(organization.subscription.status, STATUS_ACTIVE)
            self.assertEqual(organization.plan_key, PLAN_PROFESSIONAL)
            event = BillingEvent.query.filter_by(
                external_event_id="evt_old_invoice",
            ).one()
            self.assertEqual(event.status, "ignored")
            self.assertEqual(event.error_message, "STRIPE_EVENT_OUT_OF_ORDER")

    def test_invoice_without_subscription_is_ignored_without_remote_call(self):
        event = self.invoice_event("evt_invoice_no_sub", "invoice.payment_succeeded")
        event["data"]["object"].pop("subscription")

        with patch(
            "app.services.stripe_webhook_service.retrieve_subscription"
        ) as retrieve_mock:
            response = self.post_event(event)

        self.assertEqual(response.status_code, 200)
        retrieve_mock.assert_not_called()

        with self.app.app_context():
            billing_event = BillingEvent.query.filter_by(
                external_event_id="evt_invoice_no_sub",
            ).one()
            self.assertEqual(billing_event.status, "ignored")


if __name__ == "__main__":
    unittest.main()
