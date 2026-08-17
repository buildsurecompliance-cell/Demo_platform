import os
import unittest

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import BillingEvent, OrganizationMembership, User
from app.services.billing_observability import utc_now
from app.services.organizations import create_default_organization_for_user
from app.services.plan_capacity import PLAN_PROFESSIONAL, set_organization_plan
from app.services.subscription_service import (
    PROVIDER_STRIPE,
    STATUS_ACTIVE,
    STATUS_CANCELED,
    STATUS_PAST_DUE,
)


class BillingStatusTest(unittest.TestCase):

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
            BILLING_SUCCESS_URL="https://example.test/success",
            BILLING_CANCEL_URL="https://example.test/cancel",
            BILLING_PORTAL_RETURN_URL="https://example.test/portal",
        )
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()
            owner = User(email="owner@example.com", paid=False)
            owner.set_password("password123")
            admin = User(email="admin@example.com", paid=False)
            admin.set_password("password123")
            member = User(email="member@example.com", paid=False)
            member.set_password("password123")
            db.session.add_all([owner, admin, member])
            db.session.flush()
            organization = create_default_organization_for_user(owner)
            db.session.add(
                OrganizationMembership(
                    organization_id=organization.id,
                    user_id=admin.id,
                    role="ADMIN",
                )
            )
            db.session.add(
                OrganizationMembership(
                    organization_id=organization.id,
                    user_id=member.id,
                    role="MEMBER",
                )
            )
            subscription = organization.subscription
            subscription.provider = PROVIDER_STRIPE
            subscription.status = STATUS_PAST_DUE
            subscription.billing_customer_id = "cus_status_123456789"
            subscription.billing_subscription_id = "sub_status_987654321"
            subscription.current_period_end = utc_now() - timedelta(days=90)
            subscription.stripe_last_synced_at = utc_now()
            subscription.stripe_sync_error = "STRIPE_EVENT_OUT_OF_ORDER"
            set_organization_plan(organization, PLAN_PROFESSIONAL)
            db.session.add(
                BillingEvent(
                    provider="stripe",
                    external_event_id="evt_full_payload_marker",
                    event_type="customer.subscription.updated",
                    status="processed",
                    organization_id=organization.id,
                    subscription_id=subscription.id,
                    error_message="payload should not render",
                )
            )
            db.session.commit()

            self.organization_id = organization.id
            self.owner_id = owner.id
            self.admin_id = admin.id
            self.member_id = member.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def login_session(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
            session["organization_id"] = self.organization_id

    def test_owner_and_admin_can_view_status_without_external_calls(self):
        for user_id in (self.owner_id, self.admin_id):
            with self.subTest(user_id=user_id):
                self.client = self.app.test_client()
                self.login_session(user_id)

                with patch(
                    "app.services.stripe_reconciliation_service.retrieve_subscription"
                ) as retrieve_mock, patch(
                    "app.routes.billing.create_customer_portal_session"
                ) as portal_mock:
                    response = self.client.get("/billing/status")

                self.assertEqual(response.status_code, 200)
                body = response.data.decode()
                self.assertIn("Professional", body)
                self.assertIn(PROVIDER_STRIPE, body)
                self.assertIn(STATUS_PAST_DUE, body)
                self.assertIn("Blocked", body)
                self.assertIn("STRIPE_EVENT_OUT_OF_ORDER", body)
                self.assertIn("cus_****6789", body)
                self.assertIn("sub_****4321", body)
                self.assertNotIn("cus_status_123456789", body)
                self.assertNotIn("sub_status_987654321", body)
                self.assertNotIn("evt_full_payload_marker", body)
                self.assertNotIn("payload should not render", body)
                self.assertNotIn("sk_test_fake", body)
                self.assertNotIn("whsec_fake", body)
                retrieve_mock.assert_not_called()
                portal_mock.assert_not_called()

    def test_member_cannot_view_status(self):
        self.login_session(self.member_id)

        response = self.client.get("/billing/status")

        self.assertEqual(response.status_code, 403)

    def test_canceled_subscription_can_still_view_status(self):
        with self.app.app_context():
            from app.models import Organization

            organization = db.session.get(Organization, self.organization_id)
            organization.subscription.status = STATUS_CANCELED
            db.session.commit()

        self.login_session(self.owner_id)

        response = self.client.get("/billing/status")

        self.assertEqual(response.status_code, 200)
        self.assertIn(STATUS_CANCELED, response.data.decode())

    def test_basil_scheduled_cancellation_is_rendered_from_cancel_at(self):
        with self.app.app_context():
            from app.models import Organization

            organization = db.session.get(Organization, self.organization_id)
            subscription = organization.subscription
            subscription.status = STATUS_ACTIVE
            subscription.cancel_at_period_end = False
            subscription.current_period_end = datetime(
                2026,
                9,
                6,
                tzinfo=timezone.utc,
            )
            subscription.cancel_at = datetime(2026, 9, 6, tzinfo=timezone.utc)
            db.session.commit()

        self.login_session(self.owner_id)

        with patch(
            "app.services.stripe_reconciliation_service.retrieve_subscription"
        ) as retrieve_mock:
            response = self.client.get("/billing/status")

        self.assertEqual(response.status_code, 200)
        body = response.data.decode()
        self.assertIn("Scheduled cancellation", body)
        self.assertIn("Yes", body)
        self.assertIn("Cancellation date", body)
        self.assertIn("2026-09-06", body)
        self.assertIn("Operational access", body)
        self.assertIn("Allowed", body)
        self.assertNotIn("Cancel at period end", body)
        retrieve_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
