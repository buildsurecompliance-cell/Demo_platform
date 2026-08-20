import os
import unittest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import User
from app.services.organizations import create_default_organization_for_user
from app.services.subscription_service import STATUS_INACTIVE


class AuthPaidLegacyTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestingConfig)
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def csrf_token(self, path="/login"):
        response = self.client.get(path)
        match = __import__("re").search(
            r'name="csrf_token"[^>]*value="([^"]+)"',
            response.get_data(as_text=True),
        )
        self.assertIsNotNone(match)
        return match.group(1)

    def create_user_with_organization(self, email, paid):
        user = User(email=email, paid=paid)
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        organization = create_default_organization_for_user(user)
        db.session.commit()
        return user.id, organization.id

    def login(self, email, follow_redirects=True):
        return self.client.post(
            "/login",
            data={
                "email": email,
                "password": "password123",
                "csrf_token": self.csrf_token("/login"),
            },
            follow_redirects=follow_redirects,
        )

    def test_unpaid_member_with_active_organization_can_login(self):
        with self.app.app_context():
            self.create_user_with_organization("unpaid@example.com", paid=False)

        response = self.login("unpaid@example.com")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Compliance Dashboard",
            response.get_data(as_text=True),
        )

    def test_paid_user_with_inactive_subscription_logs_in_but_operational_access_blocks(self):
        with self.app.app_context():
            user_id, organization_id = self.create_user_with_organization(
                "paid-blocked@example.com",
                paid=True,
            )
            user = db.session.get(User, user_id)
            user.last_active_organization_id = organization_id
            user.organization_memberships[0].organization.subscription.status = (
                STATUS_INACTIVE
            )
            db.session.commit()

        response = self.login("paid-blocked@example.com")

        self.assertEqual(response.status_code, 403)
        self.assertIn(
            "Your organization does not currently have an active subscription.",
            response.get_data(as_text=True),
        )

    def test_user_without_membership_is_not_authorized_by_paid_flag(self):
        with self.app.app_context():
            user = User(email="no-membership@example.com", paid=True)
            user.set_password("password123")
            db.session.add(user)
            db.session.commit()

        response = self.login("no-membership@example.com")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Organization access required",
            response.get_data(as_text=True),
        )

    def test_normal_registration_keeps_paid_false_and_creates_organization(self):
        token = self.csrf_token("/register")
        response = self.client.post(
            "/register",
            data={
                "email": "new@example.com",
                "password": "password123",
                "csrf_token": token,
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            user = User.query.filter_by(email="new@example.com").one()
            self.assertFalse(user.paid)
            self.assertEqual(len(user.organization_memberships), 1)
            self.assertIsNotNone(
                user.organization_memberships[0].organization.subscription
            )

    def test_paid_column_remains_compatibility_only(self):
        with self.app.app_context():
            user_id, _ = self.create_user_with_organization(
                "compat@example.com",
                paid=True,
            )
            user = db.session.get(User, user_id)

            self.assertTrue(hasattr(user, "paid"))
            self.assertTrue(user.paid)


if __name__ == "__main__":
    unittest.main()
