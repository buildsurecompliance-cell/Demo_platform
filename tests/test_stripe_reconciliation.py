import os
import re
import unittest

from datetime import datetime, timezone
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Organization, OrganizationMembership, Subscription, User
from app.services.organizations import create_default_organization_for_user
from app.services.plan_capacity import PLAN_PROFESSIONAL, PLAN_STARTER
from app.services.stripe_reconciliation_service import reconcile_subscription
from app.services.stripe_service import StripeOperationError
from app.services.subscription_service import (
    PROVIDER_INTERNAL,
    PROVIDER_STRIPE,
    STATUS_ACTIVE,
    STATUS_PAST_DUE,
    STATUS_TRIALING,
)


def utc_datetime(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class StripeReconciliationTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=True,
            PROPAGATE_EXCEPTIONS=False,
            BILLING_PROVIDER="stripe",
            STRIPE_SECRET_KEY="sk_test_fake",
            STRIPE_STARTER_PRICE_ID="price_starter",
            STRIPE_PROFESSIONAL_PRICE_ID="price_professional",
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
            other = User(email="other@example.com", paid=False)
            other.set_password("password123")
            db.session.add_all([owner, admin, member, other])
            db.session.flush()
            organization = create_default_organization_for_user(owner)
            other_organization = create_default_organization_for_user(other)
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
            organization.subscription.provider = PROVIDER_STRIPE
            organization.subscription.billing_customer_id = "cus_reconcile"
            organization.subscription.billing_subscription_id = "sub_reconcile"
            db.session.commit()

            self.organization_id = organization.id
            self.other_organization_id = other_organization.id
            self.owner_id = owner.id
            self.admin_id = admin.id
            self.member_id = member.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def remote_subscription(
        self,
        *,
        status=STATUS_ACTIVE,
        price_id="price_professional",
        customer="cus_reconcile",
        subscription="sub_reconcile",
        organization_id=None,
        current_period_start=1710000000,
        current_period_end=1712600000,
        item_current_period_start=None,
        item_current_period_end=None,
        cancel_at=None,
        cancel_at_period_end=False,
        canceled_at=None,
    ):
        organization_id = organization_id or self.organization_id
        item = {"price": {"id": price_id}}
        if item_current_period_start is not None:
            item["current_period_start"] = item_current_period_start
        if item_current_period_end is not None:
            item["current_period_end"] = item_current_period_end

        return {
            "id": subscription,
            "customer": customer,
            "status": status,
            "current_period_start": current_period_start,
            "current_period_end": current_period_end,
            "cancel_at": cancel_at,
            "cancel_at_period_end": cancel_at_period_end,
            "canceled_at": canceled_at,
            "trial_start": 1710000000 if status == STATUS_TRIALING else None,
            "trial_end": 1710600000 if status == STATUS_TRIALING else None,
            "metadata": {"organization_id": str(organization_id)},
            "items": {"data": [item]},
        }

    def csrf_token(self):
        response = self.client.get("/login")
        match = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            response.data,
        )
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def login_session(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
            session["organization_id"] = self.organization_id

    def test_reconcile_active_trialing_past_due_and_plan_mapping(self):
        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)

            with patch(
                "app.services.stripe_reconciliation_service.retrieve_subscription",
                return_value=self.remote_subscription(status=STATUS_TRIALING),
            ):
                reconcile_subscription(organization)
                self.assertEqual(organization.subscription.status, STATUS_TRIALING)
                self.assertIsNotNone(organization.subscription.trial_end)
                self.assertEqual(organization.plan_key, PLAN_PROFESSIONAL)
                self.assertFalse(db.session.new)

            with patch(
                "app.services.stripe_reconciliation_service.retrieve_subscription",
                return_value=self.remote_subscription(
                    status=STATUS_PAST_DUE,
                    price_id="price_starter",
                ),
            ):
                reconcile_subscription(organization)
                self.assertEqual(organization.subscription.status, STATUS_PAST_DUE)
                self.assertEqual(organization.plan_key, PLAN_STARTER)

    def test_reconcile_persists_subscription_item_billing_period(self):
        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)

            with patch(
                "app.services.stripe_reconciliation_service.retrieve_subscription",
                return_value=self.remote_subscription(
                    current_period_start=None,
                    current_period_end=None,
                    item_current_period_start=1710004000,
                    item_current_period_end=1712604000,
                ),
            ):
                reconcile_subscription(organization)

            self.assertEqual(organization.plan_key, PLAN_PROFESSIONAL)
            self.assertEqual(
                utc_datetime(organization.subscription.current_period_start),
                datetime.fromtimestamp(1710004000, tz=timezone.utc),
            )
            self.assertEqual(
                utc_datetime(organization.subscription.current_period_end),
                datetime.fromtimestamp(1712604000, tz=timezone.utc),
            )

    def test_reconcile_persists_basil_scheduled_cancellation(self):
        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)

            with patch(
                "app.services.stripe_reconciliation_service.retrieve_subscription",
                return_value=self.remote_subscription(
                    current_period_start=None,
                    current_period_end=None,
                    item_current_period_start=1783296000,
                    item_current_period_end=1785974400,
                    cancel_at=1785974400,
                    cancel_at_period_end=False,
                    canceled_at=1785283200,
                ),
            ):
                reconcile_subscription(organization)

            self.assertEqual(organization.plan_key, PLAN_PROFESSIONAL)
            self.assertEqual(organization.subscription.status, STATUS_ACTIVE)
            self.assertFalse(organization.subscription.cancel_at_period_end)
            self.assertEqual(
                utc_datetime(organization.subscription.cancel_at),
                datetime.fromtimestamp(1785974400, tz=timezone.utc),
            )
            self.assertEqual(
                utc_datetime(organization.subscription.current_period_end),
                datetime.fromtimestamp(1785974400, tz=timezone.utc),
            )

    def test_reconcile_failures_do_not_mutate(self):
        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            original_plan = organization.plan_key
            original_status = organization.subscription.status

            for remote in (
                self.remote_subscription(price_id="price_unknown"),
                self.remote_subscription(customer="cus_other"),
                self.remote_subscription(subscription="sub_other"),
                self.remote_subscription(organization_id=self.other_organization_id),
            ):
                with self.subTest(remote=remote):
                    db.session.rollback()
                    organization = db.session.get(Organization, self.organization_id)
                    with patch(
                        "app.services.stripe_reconciliation_service.retrieve_subscription",
                        return_value=remote,
                    ):
                        with self.assertRaises((StripeOperationError, ValueError)):
                            reconcile_subscription(organization)
                    self.assertEqual(organization.plan_key, original_plan)
                    self.assertEqual(organization.subscription.status, original_status)

    def test_provider_internal_or_missing_subscription_id_cannot_reconcile(self):
        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            organization.subscription.provider = PROVIDER_INTERNAL
            with self.assertRaises(StripeOperationError):
                reconcile_subscription(organization)

            organization.subscription.provider = PROVIDER_STRIPE
            organization.subscription.billing_subscription_id = None
            with self.assertRaises(StripeOperationError):
                reconcile_subscription(organization)

    def test_reconcile_route_owner_admin_allowed_member_forbidden_and_commits(self):
        self.login_session(self.owner_id)
        token = self.csrf_token()
        with patch(
            "app.services.stripe_reconciliation_service.retrieve_subscription",
            return_value=self.remote_subscription(status=STATUS_ACTIVE),
        ):
            response = self.client.post(
                "/billing/reconcile",
                data={"csrf_token": token, "organization_id": self.other_organization_id},
            )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            self.assertEqual(organization.subscription.status, STATUS_ACTIVE)
            self.assertEqual(organization.plan_key, PLAN_PROFESSIONAL)

        self.client = self.app.test_client()
        self.login_session(self.member_id)
        token = self.csrf_token()
        with patch("app.routes.billing.reconcile_subscription") as reconcile_mock:
            response = self.client.post(
                "/billing/reconcile",
                data={"csrf_token": token},
            )
        self.assertEqual(response.status_code, 403)
        reconcile_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
