import os
import re
import unittest
from unittest.mock import patch

from markupsafe import escape


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import (
    Organization,
    OrganizationMembership,
    Project,
    ProjectSubcontractor,
    ROLE_ADMIN,
    ROLE_MEMBER,
    Subcontractor,
    User,
)
from app.services.document_requests import create_or_resend_coi_request
from app.services.organizations import create_default_organization_for_user


class OrganizationSettingsTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=True,
            PROPAGATE_EXCEPTIONS=False,
            APPLICATION_BASE_URL="https://buildsure.test",
            RATELIMIT_ENABLED=False,
        )
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()

        db.drop_all()
        db.create_all()

        self.owner = self.create_user("owner@example.com")
        self.admin = self.create_user("admin@example.com")
        self.member = self.create_user("member@example.com")
        self.outsider = self.create_user("outsider@example.com")

        self.organization = create_default_organization_for_user(self.owner)
        self.other_organization = create_default_organization_for_user(
            self.outsider
        )
        db.session.add_all(
            [
                OrganizationMembership(
                    organization_id=self.organization.id,
                    user_id=self.admin.id,
                    role=ROLE_ADMIN,
                ),
                OrganizationMembership(
                    organization_id=self.organization.id,
                    user_id=self.member.id,
                    role=ROLE_MEMBER,
                ),
            ]
        )
        self.project = Project(
            name="Northgate School Expansion",
            user_id=self.owner.id,
            organization_id=self.organization.id,
        )
        self.subcontractor = Subcontractor(
            name="Metro Roofing Systems",
            email="sub@example.com",
            user_id=self.owner.id,
            organization_id=self.organization.id,
        )
        db.session.add_all([self.project, self.subcontractor])
        db.session.flush()
        db.session.add(
            ProjectSubcontractor(
                project_id=self.project.id,
                subcontractor_id=self.subcontractor.id,
            )
        )
        db.session.commit()

        self.owner_id = self.owner.id
        self.admin_id = self.admin.id
        self.member_id = self.member.id
        self.organization_id = self.organization.id
        self.other_organization_id = self.other_organization.id
        self.project_id = self.project.id
        self.subcontractor_id = self.subcontractor.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.app_context.pop()

    def create_user(self, email):
        user = User(
            email=email,
            paid=True,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        return user

    def login(self, user_id, organization_id=None):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
            if organization_id:
                session["organization_id"] = organization_id

    def csrf_token(self):
        response = self.client.get("/organization/settings")
        match = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            response.data,
        )
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get("/organization/settings")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_owner_can_view_current_company_name(self):
        self.login(self.owner_id, self.organization_id)

        response = self.client.get("/organization/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Organization Settings", response.data)
        self.assertIn(b"Company Name", response.data)
        self.assertIn(
            str(escape(self.organization.name)).encode(),
            response.data,
        )
        self.assertIn(b"Used in emails and external requests.", response.data)
        self.assertIn(b"Save Changes", response.data)

    def test_owner_can_update_company_name_with_trim(self):
        self.login(self.owner_id, self.organization_id)
        csrf_token = self.csrf_token()

        response = self.client.post(
            "/organization/settings",
            data={
                "csrf_token": csrf_token,
                "name": "  BuildSure Compliance  ",
            },
        )

        self.assertEqual(response.status_code, 302)
        organization = db.session.get(Organization, self.organization_id)
        self.assertEqual(organization.name, "BuildSure Compliance")

    def test_admin_can_update_company_name(self):
        self.login(self.admin_id, self.organization_id)
        csrf_token = self.csrf_token()

        response = self.client.post(
            "/organization/settings",
            data={
                "csrf_token": csrf_token,
                "name": "Admin Edited Company",
            },
        )

        self.assertEqual(response.status_code, 302)
        organization = db.session.get(Organization, self.organization_id)
        self.assertEqual(organization.name, "Admin Edited Company")

    def test_member_cannot_view_or_update_settings(self):
        member_client = self.app.test_client()
        with member_client.session_transaction() as session:
            session["_user_id"] = str(self.member_id)
            session["_fresh"] = True
            session["organization_id"] = self.organization_id

        get_response = member_client.get("/organization/settings")
        csrf_enabled = self.app.config["WTF_CSRF_ENABLED"]
        self.app.config["WTF_CSRF_ENABLED"] = False
        post_response = member_client.post(
            "/organization/settings",
            data={
                "name": "Member Edited Company",
            },
        )
        self.app.config["WTF_CSRF_ENABLED"] = csrf_enabled

        self.assertEqual(get_response.status_code, 403)
        self.assertEqual(post_response.status_code, 403)
        organization = db.session.get(Organization, self.organization_id)
        self.assertNotEqual(organization.name, "Member Edited Company")

    def test_blank_company_name_is_rejected(self):
        self.login(self.owner_id, self.organization_id)
        csrf_token = self.csrf_token()

        response = self.client.post(
            "/organization/settings",
            data={
                "csrf_token": csrf_token,
                "name": "   ",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Company name is required.", response.data)
        organization = db.session.get(Organization, self.organization_id)
        self.assertNotEqual(organization.name, "")

    def test_name_longer_than_model_limit_is_rejected(self):
        self.login(self.owner_id, self.organization_id)
        csrf_token = self.csrf_token()

        response = self.client.post(
            "/organization/settings",
            data={
                "csrf_token": csrf_token,
                "name": "A" * 256,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"255 characters or fewer", response.data)

    def test_manipulated_fields_do_not_change_other_organization(self):
        self.login(self.owner_id, self.organization_id)
        csrf_token = self.csrf_token()
        original_other_name = self.other_organization.name

        response = self.client.post(
            "/organization/settings",
            data={
                "csrf_token": csrf_token,
                "name": "Primary Organization",
                "organization_id": str(self.other_organization_id),
                "plan_key": "ENTERPRISE",
                "owner_id": str(self.member_id),
            },
        )

        self.assertEqual(response.status_code, 302)
        organization = db.session.get(Organization, self.organization_id)
        other_organization = db.session.get(
            Organization,
            self.other_organization_id,
        )
        self.assertEqual(organization.name, "Primary Organization")
        self.assertEqual(other_organization.name, original_other_name)
        self.assertEqual(organization.plan_key, "STARTER")

    def test_html_company_name_is_escaped_on_settings_page(self):
        self.organization.name = 'Build <script>alert("x")</script>'
        db.session.commit()
        self.login(self.owner_id, self.organization_id)

        response = self.client.get("/organization/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"Build &lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;",
            response.data,
        )
        self.assertNotIn(b'<script>alert("x")</script>', response.data)

    def test_request_coi_email_uses_updated_organization_name(self):
        self.organization.name = "BuildSure Compliance"
        db.session.commit()

        with patch(
            "app.services.document_requests.send_email_reminder",
            return_value=True,
        ) as send_email:
            create_or_resend_coi_request(
                organization=self.organization,
                project=self.project,
                subcontractor=self.subcontractor,
                created_by_user_id=self.owner_id,
            )

        _, _, message = send_email.call_args.args
        html_message = send_email.call_args.kwargs["html_message"]
        self.assertIn(
            "BuildSure Compliance has requested an updated Certificate of "
            "Insurance.",
            message,
        )
        self.assertIn(
            "BuildSure Compliance has requested an updated Certificate of "
            "Insurance.",
            html_message,
        )


if __name__ == "__main__":
    unittest.main()
