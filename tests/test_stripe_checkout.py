import os
import re
import unittest

from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Organization, OrganizationMembership, User
from app.services.organizations import create_default_organization_for_user
from app.services.plan_capacity import PLAN_PROFESSIONAL
from app.services.stripe_service import (
    STRIPE_CHECKOUT_CREATE_FAILED,
    StripeOperationError,
)
from app.services.subscription_service import STATUS_INACTIVE


class StripeCheckoutRouteTest(unittest.TestCase):

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
            self.owner = User(email="owner@example.com", paid=False)
            self.owner.set_password("password123")
            self.admin = User(email="admin@example.com", paid=False)
            self.admin.set_password("password123")
            self.member = User(email="member@example.com", paid=False)
            self.member.set_password("password123")
            db.session.add_all([self.owner, self.admin, self.member])
            db.session.flush()

            self.organization = create_default_organization_for_user(self.owner)
            self.organization.subscription.status = STATUS_INACTIVE
            db.session.add(
                OrganizationMembership(
                    organization_id=self.organization.id,
                    user_id=self.admin.id,
                    role="ADMIN",
                )
            )
            db.session.add(
                OrganizationMembership(
                    organization_id=self.organization.id,
                    user_id=self.member.id,
                    role="MEMBER",
                )
            )
            db.session.commit()

            self.organization_id = self.organization.id
            self.owner_id = self.owner.id
            self.admin_id = self.admin.id
            self.member_id = self.member.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

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

    def test_checkout_requires_post_and_csrf(self):
        self.login_session(self.owner_id)

        get_response = self.client.get("/billing/checkout/PROFESSIONAL")
        missing_csrf = self.client.post("/billing/checkout/PROFESSIONAL")

        self.assertEqual(get_response.status_code, 405)
        self.assertEqual(missing_csrf.status_code, 403)

    def test_owner_can_start_checkout_without_subscription_gate(self):
        self.login_session(self.owner_id)
        token = self.csrf_token()

        with patch(
            "app.routes.billing.create_checkout_session",
            return_value={"url": "https://stripe.test/session"},
        ) as create_checkout:
            response = self.client.post(
                "/billing/checkout/PROFESSIONAL",
                data={
                    "csrf_token": token,
                    "price_id": "client_supplied_price",
                    "customer": "client_supplied_customer",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "https://stripe.test/session")
        create_checkout.assert_called_once()
        _, kwargs = create_checkout.call_args
        self.assertEqual(kwargs["plan_key"], PLAN_PROFESSIONAL)
        self.assertNotIn("price_id", kwargs)
        self.assertNotIn("customer", kwargs)

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            self.assertEqual(organization.subscription.status, STATUS_INACTIVE)

    def test_admin_can_start_checkout(self):
        self.login_session(self.admin_id)
        token = self.csrf_token()

        with patch(
            "app.routes.billing.create_checkout_session",
            return_value={"url": "https://stripe.test/session"},
        ):
            response = self.client.post(
                "/billing/checkout/PROFESSIONAL",
                data={"csrf_token": token},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "https://stripe.test/session")

    def test_member_cannot_start_checkout(self):
        self.login_session(self.member_id)
        token = self.csrf_token()

        with patch("app.routes.billing.create_checkout_session") as create_checkout:
            response = self.client.post(
                "/billing/checkout/PROFESSIONAL",
                data={"csrf_token": token},
            )

        self.assertEqual(response.status_code, 403)
        create_checkout.assert_not_called()

    def test_checkout_configuration_error_is_safe(self):
        self.login_session(self.owner_id)
        token = self.csrf_token()

        with patch(
            "app.routes.billing.create_checkout_session",
            side_effect=StripeOperationError(
                STRIPE_CHECKOUT_CREATE_FAILED,
                "boom",
            ),
        ):
            response = self.client.post(
                "/billing/checkout/PROFESSIONAL",
                data={"csrf_token": token},
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/subscribe", response.location)
        self.assertNotIn(b"boom", response.data)


if __name__ == "__main__":
    unittest.main()
