import os
import re
import unittest

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import (
    Document,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    Project,
    Subcontractor,
    Subscription,
    User,
)
from app.routes.projects import view_project
from app.services.organizations import (
    create_default_organization_for_user,
    remember_active_organization,
)
from app.services.subscription_service import (
    STATUS_ACTIVE,
    STATUS_CANCELED,
    STATUS_INACTIVE,
    STATUS_INCOMPLETE,
    STATUS_PAST_DUE,
    STATUS_PAUSED,
    STATUS_TRIALING,
    STATUS_UNPAID,
    cancel_subscription,
    schedule_cancellation,
    start_trial,
)


class SubscriptionAccessTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestingConfig)
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()

            self.owner = User(email="owner@example.com", paid=True)
            self.owner.set_password("password123")
            self.member = User(email="member@example.com", paid=False)
            self.member.set_password("password123")
            self.other = User(email="other@example.com", paid=True)
            self.other.set_password("password123")
            db.session.add_all([self.owner, self.member, self.other])
            db.session.flush()

            self.organization = create_default_organization_for_user(self.owner)
            self.other_organization = create_default_organization_for_user(
                self.other
            )
            db.session.add(
                OrganizationMembership(
                    organization_id=self.organization.id,
                    user_id=self.member.id,
                    role="MEMBER",
                )
            )
            self.project = Project(
                name="Protected Project",
                user_id=self.owner.id,
                organization_id=self.organization.id,
            )
            self.other_project = Project(
                name="Other Project",
                user_id=self.other.id,
                organization_id=self.other_organization.id,
            )
            self.subcontractor = Subcontractor(
                name="Protected Sub",
                user_id=self.owner.id,
                organization_id=self.organization.id,
                email="sub@example.com",
            )
            self.other_subcontractor = Subcontractor(
                name="Other Sub",
                user_id=self.other.id,
                organization_id=self.other_organization.id,
            )
            db.session.add_all(
                [
                    self.project,
                    self.other_project,
                    self.subcontractor,
                    self.other_subcontractor,
                ]
            )
            db.session.flush()
            self.document = Document(
                filename="missing.pdf",
                original_name="missing.pdf",
                document_type="COI",
                sub_id=self.subcontractor.id,
                uploaded_by=self.owner.id,
            )
            self.other_document = Document(
                filename="other.pdf",
                original_name="other.pdf",
                document_type="COI",
                sub_id=self.other_subcontractor.id,
                uploaded_by=self.other.id,
            )
            db.session.add_all([self.document, self.other_document])
            db.session.commit()

            self.owner_id = self.owner.id
            self.member_id = self.member.id
            self.other_id = self.other.id
            self.organization_id = self.organization.id
            self.other_organization_id = self.other_organization.id
            self.project_id = self.project.id
            self.other_project_id = self.other_project.id
            self.subcontractor_id = self.subcontractor.id
            self.document_id = self.document.id
            self.other_document_id = self.other_document.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def csrf_token(self, path="/login"):
        response = self.client.get(path)
        match = re.search(
            r'name="csrf_token"[^>]*value="([^"]+)"',
            response.get_data(as_text=True),
        )
        self.assertIsNotNone(match)
        return match.group(1)

    def login(self, email="owner@example.com"):
        token = self.csrf_token("/login")
        return self.client.post(
            "/login",
            data={
                "email": email,
                "password": "password123",
                "csrf_token": token,
            },
            follow_redirects=False,
        )

    def set_subscription_status(self, status, organization_id=None):
        with self.app.app_context():
            organization = db.session.get(
                Organization,
                organization_id or self.organization_id,
            )
            organization.subscription.status = status
            organization.subscription.cancel_at_period_end = False
            organization.subscription.trial_end = None
            organization.subscription.current_period_end = None
            db.session.commit()

    def test_unauthenticated_user_follows_login_flow(self):
        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_active_subscription_allows_dashboard(self):
        self.login()
        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Protected Project", response.get_data(as_text=True))

    def test_blocked_subscription_renders_friendly_html(self):
        self.set_subscription_status(STATUS_INACTIVE)
        self.login()

        response = self.client.get("/dashboard")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 403)
        self.assertIn("Subscription access required", body)
        self.assertIn("Inactive", body)
        self.assertIn("Starter", body)
        self.assertIn("View plans", body)
        self.assertNotIn("Traceback", body)
        self.assertNotIn("billing_customer_id", body)

    def test_member_blocked_page_does_not_show_billing_action(self):
        self.set_subscription_status(STATUS_UNPAID)
        self.login("member@example.com")

        response = self.client.get("/dashboard")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 403)
        self.assertIn("Contact your Organization owner", body)
        self.assertNotIn("View plans", body)

    def test_trialing_valid_allows_and_expired_blocks(self):
        now = datetime.now(timezone.utc)
        with self.app.app_context():
            subscription = db.session.get(
                Organization,
                self.organization_id,
            ).subscription
            start_trial(
                subscription,
                trial_start=now - timedelta(days=1),
                trial_end=now + timedelta(days=1),
            )
            db.session.commit()

        self.login()
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

        with self.app.app_context():
            subscription = db.session.get(
                Organization,
                self.organization_id,
            ).subscription
            start_trial(
                subscription,
                trial_start=now - timedelta(days=3),
                trial_end=now - timedelta(days=1),
            )
            db.session.commit()

        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 403)
        self.assertIn("Your trial has ended", response.get_data(as_text=True))

    def test_past_due_grace_allows_and_expired_blocks(self):
        now = datetime.now(timezone.utc)
        with self.app.app_context():
            subscription = db.session.get(
                Organization,
                self.organization_id,
            ).subscription
            subscription.status = STATUS_PAST_DUE
            subscription.current_period_end = now - timedelta(days=3)
            db.session.commit()

        self.login()
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

        with self.app.app_context():
            subscription = db.session.get(
                Organization,
                self.organization_id,
            ).subscription
            subscription.current_period_end = now - timedelta(days=10)
            db.session.commit()

        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 403)
        self.assertIn("grace period has ended", response.get_data(as_text=True))

    def test_blocked_statuses_block_operational_routes(self):
        for status in (
            STATUS_INACTIVE,
            STATUS_INCOMPLETE,
            STATUS_UNPAID,
            STATUS_PAUSED,
            STATUS_CANCELED,
        ):
            with self.subTest(status=status):
                self.set_subscription_status(status)
                self.login()
                response = self.client.get("/dashboard")
                self.assertEqual(response.status_code, 403)

    def test_scheduled_cancellation_respects_period_end(self):
        now = datetime.now(timezone.utc)
        with self.app.app_context():
            subscription = db.session.get(
                Organization,
                self.organization_id,
            ).subscription
            subscription.current_period_end = now + timedelta(days=1)
            schedule_cancellation(subscription)
            db.session.commit()

        self.login()
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

        with self.app.app_context():
            subscription = db.session.get(
                Organization,
                self.organization_id,
            ).subscription
            subscription.current_period_end = now - timedelta(days=1)
            db.session.commit()

        self.assertEqual(self.client.get("/dashboard").status_code, 403)

    def test_organization_without_subscription_blocks(self):
        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            db.session.delete(organization.subscription)
            db.session.commit()

        self.login()
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 403)
        self.assertIn("does not currently have", response.get_data(as_text=True))

    def test_json_blocked_response_is_structured(self):
        self.set_subscription_status(STATUS_UNPAID)
        self.login()
        token = self.csrf_token("/subscribe")

        response = self.client.post(
            f"/documents/{self.document_id}/analyze",
            data={"csrf_token": token},
            headers={"Accept": "application/json"},
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"], "subscription_access_denied")
        self.assertEqual(payload["reason_code"], "SUBSCRIPTION_UNPAID")
        self.assertEqual(payload["status"], "unpaid")
        self.assertIn("message", payload)
        self.assertNotIn("billing_customer_id", payload)

    def test_json_active_endpoint_reaches_route(self):
        self.login()
        token = self.csrf_token("/subscribe")

        response = self.client.post(
            f"/documents/999999/analyze",
            data={"csrf_token": token},
            headers={"Accept": "application/json"},
        )

        self.assertEqual(response.status_code, 404)

    def test_project_routes_require_subscription_before_capacity(self):
        self.set_subscription_status(STATUS_UNPAID)
        self.login()
        token = self.csrf_token("/subscribe")

        response = self.client.post(
            "/add_project",
            data={
                "name": "New Project",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 403)
        self.assertIn("subscription requires payment", body)
        self.assertNotIn("Project limit reached", body)

    def test_subcontractor_routes_require_subscription_before_capacity(self):
        self.set_subscription_status(STATUS_UNPAID)
        self.login()
        token = self.csrf_token("/subscribe")

        response = self.client.post(
            "/add_sub",
            data={
                "name": "New Sub",
                "csrf_token": token,
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn(
            "subscription requires payment",
            response.get_data(as_text=True),
        )

    def test_view_edit_and_document_routes_require_subscription(self):
        self.set_subscription_status(STATUS_CANCELED)
        self.login()

        endpoints = [
            f"/project/{self.project_id}",
            f"/edit_project/{self.project_id}",
            f"/sub/{self.subcontractor_id}/documents",
            f"/edit_sub/{self.subcontractor_id}",
            f"/document/{self.document_id}",
            f"/download_document/{self.document_id}",
        ]

        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(endpoint).status_code, 403)

    def test_document_from_other_tenant_not_revealed_when_active(self):
        self.login()

        response = self.client.get(
            f"/download_document/{self.other_document_id}",
            follow_redirects=True,
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Unauthorized", body)
        self.assertNotIn("Other Sub", body)

    def test_user_paid_does_not_override_subscription(self):
        self.set_subscription_status(STATUS_UNPAID)
        with self.app.app_context():
            user = db.session.get(User, self.owner_id)
            user.paid = True
            db.session.commit()

        self.login()
        self.assertEqual(self.client.get("/dashboard").status_code, 403)

    def test_user_unpaid_does_not_block_active_organization(self):
        with self.app.app_context():
            user = db.session.get(User, self.member_id)
            user.paid = False
            db.session.commit()

        self.login("member@example.com")
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_multiple_organizations_use_active_organization_subscription(self):
        with self.app.app_context():
            owner = db.session.get(User, self.owner_id)
            db.session.add(
                OrganizationMembership(
                    organization_id=self.other_organization_id,
                    user_id=owner.id,
                    role="OWNER",
                )
            )
            db.session.get(
                Organization,
                self.organization_id,
            ).subscription.status = STATUS_ACTIVE
            db.session.get(
                Organization,
                self.other_organization_id,
            ).subscription.status = STATUS_UNPAID
            db.session.commit()

        self.login()
        with self.client.session_transaction() as session:
            session["organization_id"] = self.other_organization_id

        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 403)

        with self.client.session_transaction() as session:
            session["organization_id"] = self.organization_id

        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_manipulated_session_does_not_use_other_subscription(self):
        self.login()
        with self.client.session_transaction() as session:
            session["organization_id"] = self.other_organization_id

        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            self.assertEqual(session["organization_id"], self.organization_id)

    def test_public_and_billing_routes_remain_accessible(self):
        self.set_subscription_status(STATUS_CANCELED)

        self.assertIn(
            self.client.get("/login").status_code,
            (200, 302),
        )
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/subscribe").status_code, 200)

        self.login()
        self.assertEqual(self.client.get("/subscribe").status_code, 200)

        token = self.csrf_token("/subscribe")
        logout = self.client.post(
            "/logout",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        self.assertEqual(logout.status_code, 302)

    def test_team_view_accessible_but_invite_post_blocked(self):
        self.set_subscription_status(STATUS_CANCELED)
        self.login()

        self.assertEqual(self.client.get("/team").status_code, 200)
        token = self.csrf_token("/team")
        response = self.client.post(
            "/team",
            data={
                "email": "invitee@example.com",
                "role": "MEMBER",
                "csrf_token": token,
            },
        )

        self.assertEqual(response.status_code, 403)
        with self.app.app_context():
            self.assertEqual(
                OrganizationInvitation.query.filter_by(
                    email="invitee@example.com",
                ).count(),
                0,
            )

    def test_blocked_request_has_no_side_effects(self):
        self.set_subscription_status(STATUS_CANCELED)
        self.login()

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            before = (
                organization.subscription.status,
                organization.plan_key,
                Project.query.count(),
                Subcontractor.query.count(),
                OrganizationMembership.query.count(),
            )

        with patch.object(db.session, "commit") as commit:
            response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 403)
        commit.assert_not_called()

        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            after = (
                organization.subscription.status,
                organization.plan_key,
                Project.query.count(),
                Subcontractor.query.count(),
                OrganizationMembership.query.count(),
            )
        self.assertEqual(before, after)

    def test_decorator_preserves_metadata(self):
        self.assertEqual(view_project.__name__, "view_project")


if __name__ == "__main__":
    unittest.main()
