import os
import re
import unittest

from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import OrganizationMembership, User
from app.services.organizations import create_default_organization_for_user
from app.services.stripe_service import (
    STRIPE_CUSTOMER_REQUIRED,
    StripeOperationError,
)


class StripePortalRouteTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=True,
            PROPAGATE_EXCEPTIONS=False,
            BILLING_PROVIDER="stripe",
            STRIPE_SECRET_KEY="sk_test_fake",
        )
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()
            owner = User(email="owner@example.com", paid=False)
            owner.set_password("password123")
            member = User(email="member@example.com", paid=False)
            member.set_password("password123")
            db.session.add_all([owner, member])
            db.session.flush()
            organization = create_default_organization_for_user(owner)
            db.session.add(
                OrganizationMembership(
                    organization_id=organization.id,
                    user_id=member.id,
                    role="MEMBER",
                )
            )
            db.session.commit()

            self.owner_id = owner.id
            self.member_id = member.id
            self.organization_id = organization.id

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

    def test_portal_requires_post_and_csrf(self):
        self.login_session(self.owner_id)

        get_response = self.client.get("/billing/portal")
        missing_csrf = self.client.post("/billing/portal")

        self.assertEqual(get_response.status_code, 405)
        self.assertEqual(missing_csrf.status_code, 403)

    def test_owner_can_open_billing_portal(self):
        self.login_session(self.owner_id)
        token = self.csrf_token()

        with patch(
            "app.routes.billing.create_customer_portal_session",
            return_value={"url": "https://stripe.test/portal"},
        ) as create_portal:
            response = self.client.post(
                "/billing/portal",
                data={"csrf_token": token},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "https://stripe.test/portal")
        create_portal.assert_called_once()

    def test_member_cannot_open_billing_portal(self):
        self.login_session(self.member_id)
        token = self.csrf_token()

        with patch("app.routes.billing.create_customer_portal_session") as create_portal:
            response = self.client.post(
                "/billing/portal",
                data={"csrf_token": token},
            )

        self.assertEqual(response.status_code, 403)
        create_portal.assert_not_called()

    def test_missing_customer_uses_safe_message(self):
        self.login_session(self.owner_id)
        token = self.csrf_token()

        with patch(
            "app.routes.billing.create_customer_portal_session",
            side_effect=StripeOperationError(
                STRIPE_CUSTOMER_REQUIRED,
                "customer missing",
            ),
        ):
            response = self.client.post(
                "/billing/portal",
                data={"csrf_token": token},
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/subscribe", response.location)


if __name__ == "__main__":
    unittest.main()
