import re
import unittest

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import (
    Document,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    Project,
    ProjectSubcontractor,
    ROLE_ADMIN,
    ROLE_MEMBER,
    ROLE_OWNER,
    Subcontractor,
    User,
)
from app.services.organizations import (
    create_default_organization_for_user,
    get_current_organization,
    hash_invitation_token,
)
from app.services.subscription_service import (
    STATUS_ACTIVE,
    get_or_create_subscription,
)


class OrganizationTenancyTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=True,
            PROPAGATE_EXCEPTIONS=False,
            RATELIMIT_ENABLED=False,
        )
        self.client = self.app.test_client()

        with self.app.app_context():
            db.drop_all()
            db.create_all()

            self.owner = self.create_user("owner@example.com")
            self.member = self.create_user("member@example.com")
            self.outsider = self.create_user("outsider@example.com")
            self.admin = self.create_user("admin@example.com")

            self.organization = create_default_organization_for_user(
                self.owner
            )
            db.session.add(
                OrganizationMembership(
                    organization_id=self.organization.id,
                    user_id=self.member.id,
                    role=ROLE_MEMBER,
                )
            )
            db.session.add(
                OrganizationMembership(
                    organization_id=self.organization.id,
                    user_id=self.admin.id,
                    role=ROLE_ADMIN,
                )
            )

            self.other_organization = create_default_organization_for_user(
                self.outsider
            )
            db.session.commit()

            self.owner_id = self.owner.id
            self.member_id = self.member.id
            self.admin_id = self.admin.id
            self.outsider_id = self.outsider.id
            self.organization_id = self.organization.id
            self.other_organization_id = self.other_organization.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def create_user(self, email):
        user = User(
            email=email,
            paid=True,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        return user

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True

    def csrf_token(self, path):
        response = self.client.get(path)
        match = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            response.data,
        )
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def test_register_creates_default_organization_and_owner_membership(self):
        token = self.csrf_token("/register")
        response = self.client.post(
            "/register",
            data={
                "email": "new-owner@example.com",
                "password": "password123",
                "csrf_token": token,
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            user = User.query.filter_by(
                email="new-owner@example.com"
            ).one()
            membership = OrganizationMembership.query.filter_by(
                user_id=user.id
            ).one()

            self.assertEqual(membership.role, ROLE_OWNER)
            self.assertIsNotNone(membership.organization)
            self.assertEqual(
                user.last_active_organization_id,
                membership.organization_id,
            )

    def test_same_organization_members_see_shared_projects_and_subs(self):
        with self.app.app_context():
            project = Project(
                name="Shared Project",
                user_id=self.owner_id,
                organization_id=self.organization_id,
            )
            sub = Subcontractor(
                name="Shared Sub",
                user_id=self.owner_id,
                organization_id=self.organization_id,
            )
            db.session.add_all([project, sub])
            db.session.commit()

        self.login(self.member_id)
        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Shared Project", response.data)
        self.assertIn(b"Shared Sub", response.data)

    def test_different_organizations_are_isolated(self):
        with self.app.app_context():
            project = Project(
                name="Private Project",
                user_id=self.owner_id,
                organization_id=self.organization_id,
            )
            sub = Subcontractor(
                name="Private Sub",
                user_id=self.owner_id,
                organization_id=self.organization_id,
            )
            db.session.add_all([project, sub])
            db.session.commit()
            project_id = project.id
            sub_id = sub.id

        self.login(self.outsider_id)

        self.assertEqual(
            self.client.get(f"/project/{project_id}").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/sub/{sub_id}/documents").status_code,
            404,
        )

    def test_matching_user_id_does_not_grant_cross_organization_access(self):
        with self.app.app_context():
            project = Project(
                name="Same User Wrong Tenant",
                user_id=self.owner_id,
                organization_id=self.other_organization_id,
            )
            sub = Subcontractor(
                name="Same User Wrong Tenant Sub",
                user_id=self.owner_id,
                organization_id=self.other_organization_id,
            )
            db.session.add_all([project, sub])
            db.session.commit()
            project_id = project.id
            sub_id = sub.id

        self.login(self.owner_id)

        self.assertEqual(
            self.client.get(f"/project/{project_id}").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/sub/{sub_id}/documents").status_code,
            404,
        )

    def test_changing_user_id_does_not_change_project_or_sub_ownership(self):
        with self.app.app_context():
            project = Project(
                name="Organization Owned Project",
                user_id=self.owner_id,
                organization_id=self.organization_id,
            )
            sub = Subcontractor(
                name="Organization Owned Sub",
                user_id=self.owner_id,
                organization_id=self.organization_id,
            )
            db.session.add_all([project, sub])
            db.session.flush()
            project.user_id = self.outsider_id
            sub.user_id = self.outsider_id
            db.session.commit()
            project_id = project.id
            sub_id = sub.id

        self.login(self.owner_id)
        self.assertEqual(
            self.client.get(f"/project/{project_id}").status_code,
            200,
        )
        self.assertEqual(
            self.client.get(f"/sub/{sub_id}/documents").status_code,
            200,
        )

        self.login(self.outsider_id)
        self.assertEqual(
            self.client.get(f"/project/{project_id}").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/sub/{sub_id}/documents").status_code,
            404,
        )

    def test_project_and_subcontractor_require_organization_id(self):
        with self.app.app_context():
            db.session.add(
                Project(
                    name="No Tenant Project",
                    user_id=self.owner_id,
                )
            )

            with self.assertRaises(IntegrityError):
                db.session.commit()

            db.session.rollback()
            db.session.add(
                Subcontractor(
                    name="No Tenant Sub",
                    user_id=self.owner_id,
                )
            )

            with self.assertRaises(IntegrityError):
                db.session.commit()

            db.session.rollback()

    def test_dashboard_uses_active_organization_not_user_only(self):
        with self.app.app_context():
            project = Project(
                name="Organization Metric Project",
                user_id=self.owner_id,
                organization_id=self.organization_id,
                contract_value=1000,
            )
            db.session.add(project)
            db.session.commit()

        self.login(self.admin_id)
        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Organization Metric Project", response.data)

    def test_session_organization_id_must_belong_to_current_user(self):
        self.login(self.owner_id)

        with self.client.session_transaction() as session:
            session["organization_id"] = self.other_organization_id

        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)

        with self.client.session_transaction() as session:
            self.assertEqual(
                session["organization_id"],
                self.organization_id,
            )

    def test_user_without_membership_gets_no_implicit_organization(self):
        with self.app.app_context():
            orphan = self.create_user("orphan@example.com")
            private_project = Project(
                name="Not Orphan Project",
                user_id=self.owner_id,
                organization_id=self.organization_id,
            )
            db.session.add(private_project)
            db.session.commit()
            orphan_id = orphan.id

        self.login(orphan_id)
        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b"Not Orphan Project", response.data)

        with self.client.session_transaction() as session:
            self.assertNotIn("organization_id", session)

    def test_first_membership_is_deterministic_fallback_for_multi_org_user(self):
        with self.app.app_context():
            second_org = Organization(
                name="Second Organization"
            )
            db.session.add(second_org)
            db.session.flush()
            get_or_create_subscription(
                second_org,
                status=STATUS_ACTIVE,
            )
            db.session.add(
                OrganizationMembership(
                    organization_id=second_org.id,
                    user_id=self.owner_id,
                    role=ROLE_MEMBER,
                )
            )
            db.session.commit()
            second_org_id = second_org.id

        self.login(self.owner_id)

        with self.client.session_transaction() as session:
            session["organization_id"] = second_org_id

        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)

        with self.client.session_transaction() as session:
            self.assertEqual(session["organization_id"], second_org_id)

        with self.client.session_transaction() as session:
            session["organization_id"] = 999999

        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)

        with self.client.session_transaction() as session:
            self.assertEqual(session["organization_id"], self.organization_id)

    def test_last_active_organization_is_restored_on_login(self):
        with self.app.app_context():
            invited_org = Organization(name="Invited Organization")
            db.session.add(invited_org)
            db.session.flush()
            get_or_create_subscription(
                invited_org,
                status=STATUS_ACTIVE,
            )
            db.session.add(
                OrganizationMembership(
                    organization_id=invited_org.id,
                    user_id=self.owner_id,
                    role=ROLE_MEMBER,
                )
            )
            user = db.session.get(User, self.owner_id)
            user.last_active_organization_id = invited_org.id
            db.session.commit()
            invited_org_id = invited_org.id

        token = self.csrf_token("/login")
        response = self.client.post(
            "/login",
            data={
                "email": "owner@example.com",
                "password": "password123",
                "csrf_token": token,
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)

        with self.client.session_transaction() as session:
            self.assertEqual(session["organization_id"], invited_org_id)

    def test_invalid_last_active_organization_is_discarded_on_login(self):
        with self.app.app_context():
            user = db.session.get(User, self.owner_id)
            user.last_active_organization_id = self.other_organization_id
            db.session.commit()

        token = self.csrf_token("/login")
        response = self.client.post(
            "/login",
            data={
                "email": "owner@example.com",
                "password": "password123",
                "csrf_token": token,
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)

        with self.client.session_transaction() as session:
            self.assertEqual(session["organization_id"], self.organization_id)

        with self.app.app_context():
            user = db.session.get(User, self.owner_id)
            self.assertEqual(
                user.last_active_organization_id,
                self.organization_id,
            )

    def test_member_cannot_manage_team_and_csrf_is_required(self):
        self.login(self.member_id)

        token = self.csrf_token("/add_project")
        response = self.client.post(
            "/team",
            data={
                "email": "candidate@example.com",
                "role": ROLE_MEMBER,
                "csrf_token": token,
            },
        )

        self.assertEqual(response.status_code, 403)

        self.login(self.owner_id)
        response = self.client.post(
            "/team",
            data={
                "email": "candidate@example.com",
                "role": ROLE_MEMBER,
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_owner_and_admin_can_create_invitation_with_hashed_token(self):
        self.login(self.owner_id)
        token = self.csrf_token("/team")
        response = self.client.post(
            "/team",
            data={
                "email": "candidate@example.com",
                "role": ROLE_MEMBER,
                "csrf_token": token,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Development invitation link:", response.data)

        with self.app.app_context():
            invitation = OrganizationInvitation.query.filter_by(
                email="candidate@example.com"
            ).one()
            self.assertNotIn(
                "candidate@example.com",
                invitation.token_hash,
            )
            self.assertEqual(len(invitation.token_hash), 64)

        self.login(self.admin_id)
        token = self.csrf_token("/team")
        response = self.client.post(
            "/team",
            data={
                "email": "admin-invite@example.com",
                "role": ROLE_MEMBER,
                "csrf_token": token,
            },
        )
        self.assertEqual(response.status_code, 200)

    def test_production_does_not_display_invitation_token(self):
        self.app.config["ENV"] = "production"
        self.login(self.owner_id)
        token = self.csrf_token("/team")

        response = self.client.post(
            "/team",
            data={
                "email": "prod-candidate@example.com",
                "role": ROLE_MEMBER,
                "csrf_token": token,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Development invitation link:", response.data)

    def test_invalid_role_and_duplicate_pending_invitation_are_rejected(self):
        self.login(self.owner_id)
        token = self.csrf_token("/team")

        invalid = self.client.post(
            "/team",
            data={
                "email": "candidate@example.com",
                "role": "BILLING",
                "csrf_token": token,
            },
        )
        self.assertEqual(invalid.status_code, 200)

        first_token = self.csrf_token("/team")
        first = self.client.post(
            "/team",
            data={
                "email": "candidate@example.com",
                "role": ROLE_MEMBER,
                "csrf_token": first_token,
            },
        )
        self.assertEqual(first.status_code, 200)

        second_token = self.csrf_token("/team")
        second = self.client.post(
            "/team",
            data={
                "email": "candidate@example.com",
                "role": ROLE_MEMBER,
                "csrf_token": second_token,
            },
        )
        self.assertEqual(second.status_code, 200)

        with self.app.app_context():
            self.assertEqual(
                OrganizationInvitation.query.filter_by(
                    email="candidate@example.com"
                ).count(),
                1,
            )

    def test_accept_invitation_creates_membership_and_is_single_use(self):
        with self.app.app_context():
            invited = self.create_user("invitee@example.com")
            token = "plain-token"
            invitation = OrganizationInvitation(
                organization_id=self.organization_id,
                email="invitee@example.com",
                role=ROLE_MEMBER,
                token_hash=hash_invitation_token(token),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                invited_by=self.owner_id,
            )
            db.session.add(invitation)
            db.session.commit()
            invited_id = invited.id

        self.login(invited_id)
        csrf = self.csrf_token(f"/team/invitations/{token}/accept")
        response = self.client.post(
            f"/team/invitations/{token}/accept",
            data={"csrf_token": csrf},
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            self.assertIsNotNone(
                OrganizationMembership.query.filter_by(
                    organization_id=self.organization_id,
                    user_id=invited_id,
                ).first()
            )
            self.assertIsNotNone(
                OrganizationInvitation.query.filter_by(
                    token_hash=hash_invitation_token(token)
                ).one().accepted_at
            )
            user = db.session.get(User, invited_id)
            self.assertEqual(
                user.last_active_organization_id,
                self.organization_id,
            )

        with self.client.session_transaction() as session:
            self.assertEqual(session["organization_id"], self.organization_id)

        csrf = self.csrf_token(f"/team/invitations/{token}/accept")
        reused = self.client.post(
            f"/team/invitations/{token}/accept",
            data={"csrf_token": csrf},
        )

        self.assertEqual(reused.status_code, 404)

    def test_invitation_rejects_wrong_email_and_expired_token(self):
        with self.app.app_context():
            wrong_token = "wrong-email-token"
            expired_token = "expired-token"
            db.session.add_all(
                [
                    OrganizationInvitation(
                        organization_id=self.organization_id,
                        email="someone-else@example.com",
                        role=ROLE_MEMBER,
                        token_hash=hash_invitation_token(wrong_token),
                        expires_at=(
                            datetime.now(timezone.utc)
                            + timedelta(days=1)
                        ),
                        invited_by=self.owner_id,
                    ),
                    OrganizationInvitation(
                        organization_id=self.organization_id,
                        email="member@example.com",
                        role=ROLE_MEMBER,
                        token_hash=hash_invitation_token(expired_token),
                        expires_at=(
                            datetime.now(timezone.utc)
                            - timedelta(days=1)
                        ),
                        invited_by=self.owner_id,
                    ),
                ]
            )
            db.session.commit()

        self.login(self.member_id)

        csrf = self.csrf_token(f"/team/invitations/{wrong_token}/accept")
        wrong = self.client.post(
            f"/team/invitations/{wrong_token}/accept",
            data={"csrf_token": csrf},
        )
        self.assertEqual(wrong.status_code, 404)

        csrf = self.csrf_token(f"/team/invitations/{expired_token}/accept")
        expired = self.client.post(
            f"/team/invitations/{expired_token}/accept",
            data={"csrf_token": csrf},
        )
        self.assertEqual(expired.status_code, 404)

    def test_invitation_get_redirects_new_user_to_register_with_token(self):
        with self.app.app_context():
            token = "new-user-token"
            db.session.add(
                OrganizationInvitation(
                    organization_id=self.organization_id,
                    email="new-invitee@example.com",
                    role=ROLE_MEMBER,
                    token_hash=hash_invitation_token(token),
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                    invited_by=self.owner_id,
                )
            )
            db.session.commit()

        response = self.client.get(
            f"/team/invitations/{token}/accept"
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/register", response.location)
        self.assertIn("invitation_token=", response.location)

    def test_register_from_invitation_creates_only_invited_membership(self):
        with self.app.app_context():
            token = "register-invite-token"
            db.session.add(
                OrganizationInvitation(
                    organization_id=self.organization_id,
                    email="fresh-invitee@example.com",
                    role=ROLE_MEMBER,
                    token_hash=hash_invitation_token(token),
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                    invited_by=self.owner_id,
                )
            )
            db.session.commit()

        response = self.client.get(f"/team/invitations/{token}/accept")
        self.assertEqual(response.status_code, 302)
        register_path = response.location
        csrf = self.csrf_token(register_path)
        response = self.client.post(
            register_path,
            data={
                "email": "fresh-invitee@example.com",
                "password": "password123",
                "invitation_token": token,
                "csrf_token": csrf,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/team/invitations/{token}/accept", response.location)

        with self.app.app_context():
            user = User.query.filter_by(
                email="fresh-invitee@example.com"
            ).one()
            self.assertEqual(
                OrganizationMembership.query.filter_by(user_id=user.id).count(),
                0,
            )
            self.assertFalse(user.paid)

        csrf = self.csrf_token(f"/team/invitations/{token}/accept")
        response = self.client.post(
            f"/team/invitations/{token}/accept",
            data={"csrf_token": csrf},
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            user = User.query.filter_by(
                email="fresh-invitee@example.com"
            ).one()
            memberships = OrganizationMembership.query.filter_by(
                user_id=user.id
            ).all()

            self.assertEqual(len(memberships), 1)
            self.assertEqual(memberships[0].organization_id, self.organization_id)
            self.assertEqual(memberships[0].role, ROLE_MEMBER)
            self.assertEqual(
                user.last_active_organization_id,
                self.organization_id,
            )
            self.assertIsNotNone(
                OrganizationInvitation.query.filter_by(
                    token_hash=hash_invitation_token(token)
                ).one().accepted_at
            )

    def test_register_from_invitation_rejects_different_email(self):
        with self.app.app_context():
            token = "wrong-register-email-token"
            db.session.add(
                OrganizationInvitation(
                    organization_id=self.organization_id,
                    email="expected@example.com",
                    role=ROLE_MEMBER,
                    token_hash=hash_invitation_token(token),
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                    invited_by=self.owner_id,
                )
            )
            db.session.commit()

        register_path = f"/register?invitation_token={token}"
        csrf = self.csrf_token(register_path)
        response = self.client.post(
            register_path,
            data={
                "email": "different@example.com",
                "password": "password123",
                "invitation_token": token,
                "csrf_token": csrf,
            },
        )

        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            self.assertIsNone(
                User.query.filter_by(email="different@example.com").first()
            )

    def test_register_from_used_or_expired_invitation_is_rejected(self):
        with self.app.app_context():
            used_token = "used-register-token"
            expired_token = "expired-register-token"
            db.session.add_all(
                [
                    OrganizationInvitation(
                        organization_id=self.organization_id,
                        email="used-register@example.com",
                        role=ROLE_MEMBER,
                        token_hash=hash_invitation_token(used_token),
                        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                        accepted_at=datetime.now(timezone.utc),
                        invited_by=self.owner_id,
                    ),
                    OrganizationInvitation(
                        organization_id=self.organization_id,
                        email="expired-register@example.com",
                        role=ROLE_MEMBER,
                        token_hash=hash_invitation_token(expired_token),
                        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
                        invited_by=self.owner_id,
                    ),
                ]
            )
            db.session.commit()

        self.assertEqual(
            self.client.get(f"/register?invitation_token={used_token}").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                f"/register?invitation_token={expired_token}"
            ).status_code,
            404,
        )

    def test_invited_member_login_returns_to_inviting_organization(self):
        with self.app.app_context():
            personal_owner = self.create_user("dual@example.com")
            personal_org = create_default_organization_for_user(personal_owner)
            token = "dual-org-token"
            db.session.add(
                OrganizationInvitation(
                    organization_id=self.organization_id,
                    email="dual@example.com",
                    role=ROLE_ADMIN,
                    token_hash=hash_invitation_token(token),
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                    invited_by=self.owner_id,
                )
            )
            db.session.commit()
            personal_owner_id = personal_owner.id
            personal_org_id = personal_org.id

        self.login(personal_owner_id)
        csrf = self.csrf_token(f"/team/invitations/{token}/accept")
        response = self.client.post(
            f"/team/invitations/{token}/accept",
            data={"csrf_token": csrf},
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            memberships = OrganizationMembership.query.filter_by(
                user_id=personal_owner_id
            ).all()
            self.assertEqual(len(memberships), 2)
            self.assertIsNotNone(
                OrganizationMembership.query.filter_by(
                    user_id=personal_owner_id,
                    organization_id=personal_org_id,
                    role=ROLE_OWNER,
                ).first()
            )
            self.assertIsNotNone(
                OrganizationMembership.query.filter_by(
                    user_id=personal_owner_id,
                    organization_id=self.organization_id,
                    role=ROLE_ADMIN,
                ).first()
            )

        logout_csrf = self.csrf_token("/dashboard")
        self.client.post(
            "/logout",
            data={"csrf_token": logout_csrf},
        )
        login_csrf = self.csrf_token("/login")
        response = self.client.post(
            "/login",
            data={
                "email": "dual@example.com",
                "password": "password123",
                "csrf_token": login_csrf,
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)

        with self.client.session_transaction() as session:
            self.assertEqual(session["organization_id"], self.organization_id)

        with self.app.app_context():
            user = db.session.get(User, personal_owner_id)
            self.assertEqual(
                user.last_active_organization_id,
                self.organization_id,
            )

    def test_incorrect_invitation_token_fails(self):
        self.login(self.member_id)
        csrf = self.csrf_token("/team/invitations/not-a-real-token/accept")
        response = self.client.post(
            "/team/invitations/not-a-real-token/accept",
            data={"csrf_token": csrf},
        )

        self.assertEqual(response.status_code, 404)

    def test_project_subcontractor_cross_organization_link_is_rejected(self):
        with self.app.app_context():
            project = Project(
                name="Owned Project",
                user_id=self.owner_id,
                organization_id=self.organization_id,
            )
            sub = Subcontractor(
                name="Outsider Sub",
                user_id=self.outsider_id,
                organization_id=self.other_organization_id,
            )
            db.session.add_all([project, sub])
            db.session.flush()
            db.session.add(
                ProjectSubcontractor(
                    project_id=project.id,
                    subcontractor_id=sub.id,
                )
            )

            with self.assertRaises(ValueError):
                db.session.commit()

            db.session.rollback()

    def test_register_rolls_back_if_organization_creation_fails(self):
        token = self.csrf_token("/register")

        with patch(
            "app.routes.auth.create_default_organization_for_user",
            side_effect=RuntimeError("membership failed"),
        ):
            response = self.client.post(
                "/register",
                data={
                    "email": "rollback@example.com",
                    "password": "password123",
                    "csrf_token": token,
                },
            )

        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            self.assertIsNone(
                User.query.filter_by(email="rollback@example.com").first()
            )

    def test_storage_and_ai_are_not_called_before_authorization(self):
        with self.app.app_context():
            sub = Subcontractor(
                name="Other Org Sub",
                user_id=self.outsider_id,
                organization_id=self.other_organization_id,
            )
            db.session.add(sub)
            db.session.flush()
            document = Document(
                filename="secret.pdf",
                original_name="secret.pdf",
                document_type="COI",
                sub_id=sub.id,
                uploaded_by=self.outsider_id,
            )
            db.session.add(document)
            db.session.commit()
            document_id = document.id

        self.login(self.owner_id)

        with patch(
            "app.routes.documents.document_exists"
        ) as document_exists, patch(
            "app.routes.documents.get_document_response"
        ) as document_response:
            response = self.client.get(f"/document/{document_id}")

        self.assertEqual(response.status_code, 302)
        document_exists.assert_not_called()
        document_response.assert_not_called()

        csrf = self.csrf_token("/add_project")
        with patch(
            "app.routes.documents.delete_document_file"
        ) as delete_document_file:
            response = self.client.post(
                f"/delete_document/{document_id}",
                data={"csrf_token": csrf},
            )

        self.assertEqual(response.status_code, 302)
        delete_document_file.assert_not_called()

        with patch(
            "app.routes.ai.analyze_and_save_document"
        ) as analyze_document:
            csrf = self.csrf_token("/add_project")
            response = self.client.post(
                f"/documents/{document_id}/analyze",
                data={"csrf_token": csrf},
            )

        self.assertEqual(response.status_code, 404)
        analyze_document.assert_not_called()

    def test_reminder_email_not_called_before_authorization(self):
        with self.app.app_context():
            sub = Subcontractor(
                name="Other Org Reminder",
                email="sub@example.com",
                user_id=self.outsider_id,
                organization_id=self.other_organization_id,
            )
            db.session.add(sub)
            db.session.commit()
            sub_id = sub.id

        self.login(self.owner_id)
        csrf = self.csrf_token("/add_project")

        with patch(
            "app.routes.notifications.send_email_reminder"
        ) as send_email:
            response = self.client.post(
                f"/send_reminder/{sub_id}",
                data={"csrf_token": csrf},
            )

        self.assertEqual(response.status_code, 404)
        send_email.assert_not_called()


if __name__ == "__main__":
    unittest.main()
