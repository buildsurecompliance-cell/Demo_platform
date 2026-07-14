import os
import tempfile
import unittest

from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import (
    Organization,
    OrganizationMembership,
    Project,
    ProjectSubcontractor,
    Subcontractor,
    User,
)
from app.services.organizations import create_default_organization_for_user
from app.services.plan_capacity import (
    ENTERPRISE,
    PROFESSIONAL,
    STARTER,
    PLAN_DEFINITIONS,
    PlanCapacityError,
    can_create_project,
    can_create_subcontractor,
    get_organization_plan,
    get_organization_usage,
    require_project_capacity,
    require_subcontractor_capacity,
    set_organization_plan,
    validate_plan_key,
)


class PlanCapacityTest(unittest.TestCase):

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
            db.session.add(
                OrganizationMembership(
                    organization_id=self.organization.id,
                    user_id=self.member.id,
                    role="MEMBER",
                )
            )
            self.other_organization = create_default_organization_for_user(
                self.other
            )
            db.session.commit()

            self.organization_id = self.organization.id
            self.other_organization_id = self.other_organization.id
            self.owner_id = self.owner.id
            self.member_id = self.member.id
            self.other_id = self.other.id

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

    def login(self, email="owner@example.com", password="password123"):
        token = self.csrf_token("/login")
        return self.client.post(
            "/login",
            data={
                "email": email,
                "password": password,
                "csrf_token": token,
            },
            follow_redirects=True,
        )

    def create_projects(self, organization_id, count):
        for index in range(count):
            db.session.add(
                Project(
                    name=f"Project {organization_id}-{index}",
                    user_id=self.owner_id,
                    organization_id=organization_id,
                )
            )
        db.session.commit()

    def create_subcontractors(self, organization_id, count):
        for index in range(count):
            db.session.add(
                Subcontractor(
                    name=f"Sub {organization_id}-{index}",
                    user_id=self.owner_id,
                    organization_id=organization_id,
                )
            )
        db.session.commit()

    def test_plan_registry_limits_are_centralized(self):
        self.assertEqual(
            set(PLAN_DEFINITIONS.keys()),
            {STARTER, PROFESSIONAL, ENTERPRISE},
        )

        starter = PLAN_DEFINITIONS[STARTER]
        professional = PLAN_DEFINITIONS[PROFESSIONAL]
        enterprise = PLAN_DEFINITIONS[ENTERPRISE]

        self.assertEqual(starter.max_projects, 10)
        self.assertEqual(starter.max_subcontractors, 25)
        self.assertTrue(starter.unlimited_users)
        self.assertEqual(professional.max_projects, 50)
        self.assertEqual(professional.max_subcontractors, 300)
        self.assertIsNone(enterprise.max_projects)
        self.assertIsNone(enterprise.max_subcontractors)
        self.assertTrue(enterprise.unlimited_users)
        self.assertFalse(hasattr(starter, "price"))
        self.assertFalse(hasattr(professional, "price"))
        self.assertFalse(hasattr(enterprise, "price"))

        with self.assertRaises(Exception):
            starter.max_projects = 99
        with self.assertRaises(TypeError):
            PLAN_DEFINITIONS["TEAM"] = starter

    def test_invalid_plan_key_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_plan_key("TEAM")

    def test_capacity_requires_active_organization(self):
        with self.assertRaises(ValueError):
            get_organization_plan(None)
        with self.assertRaises(ValueError):
            get_organization_usage(None)
        with self.assertRaises(ValueError):
            can_create_project(None)
        with self.assertRaises(ValueError):
            set_organization_plan(None, STARTER)

    def test_new_organization_defaults_to_starter(self):
        with self.app.app_context():
            organization = Organization(name="New Org")
            db.session.add(organization)
            db.session.commit()

            self.assertEqual(organization.plan_key, STARTER)
            self.assertEqual(get_organization_plan(organization).key, STARTER)

    def test_invalid_direct_plan_key_fails_on_commit(self):
        with self.app.app_context():
            organization = Organization(
                name="Invalid Plan Org",
                plan_key="TEAM",
            )
            db.session.add(organization)

            with self.assertRaises(IntegrityError):
                db.session.commit()

            db.session.rollback()

    def test_starter_allows_ten_projects_and_blocks_eleventh(self):
        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            self.create_projects(organization.id, 9)
            self.assertTrue(can_create_project(organization).allowed)

            self.create_projects(organization.id, 1)
            check = can_create_project(organization)

            self.assertFalse(check.allowed)
            self.assertEqual(check.current, 10)
            self.assertEqual(check.limit, 10)
            self.assertEqual(check.reason_code, "PROJECT_LIMIT_REACHED")
            with self.assertRaises(PlanCapacityError):
                require_project_capacity(organization)

    def test_starter_allows_twenty_five_subcontractors_and_blocks_next(self):
        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            self.create_subcontractors(organization.id, 24)
            self.assertTrue(can_create_subcontractor(organization).allowed)

            self.create_subcontractors(organization.id, 1)
            check = can_create_subcontractor(organization)

            self.assertFalse(check.allowed)
            self.assertEqual(check.current, 25)
            self.assertEqual(check.limit, 25)
            self.assertEqual(check.reason_code, "SUBCONTRACTOR_LIMIT_REACHED")
            with self.assertRaises(PlanCapacityError):
                require_subcontractor_capacity(organization)

    def test_professional_limits_are_used(self):
        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            set_organization_plan(organization, PROFESSIONAL)
            db.session.commit()

            self.create_projects(organization.id, 49)
            self.create_subcontractors(organization.id, 299)

            self.assertTrue(can_create_project(organization).allowed)
            self.assertTrue(can_create_subcontractor(organization).allowed)

            self.create_projects(organization.id, 1)
            self.create_subcontractors(organization.id, 1)

            self.assertEqual(can_create_project(organization).current, 50)
            self.assertEqual(can_create_subcontractor(organization).current, 300)
            self.assertFalse(can_create_project(organization).allowed)
            self.assertFalse(can_create_subcontractor(organization).allowed)

    def test_enterprise_never_blocks_capacity(self):
        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            set_organization_plan(organization, ENTERPRISE)
            db.session.commit()

            self.create_projects(organization.id, 55)
            self.create_subcontractors(organization.id, 305)

            self.assertTrue(can_create_project(organization).allowed)
            self.assertTrue(can_create_subcontractor(organization).allowed)

    def test_delete_releases_capacity_and_edit_does_not_consume(self):
        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            self.create_projects(organization.id, 10)
            project = Project.query.filter_by(
                organization_id=organization.id
            ).first()
            project.name = "Edited"
            db.session.commit()

            self.assertFalse(can_create_project(organization).allowed)

            db.session.delete(project)
            db.session.commit()

            self.assertTrue(can_create_project(organization).allowed)

    def test_project_subcontractor_links_do_not_increase_subcontractor_usage(self):
        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            project = Project(
                name="Linked Project",
                user_id=self.owner_id,
                organization_id=organization.id,
            )
            sub = Subcontractor(
                name="Linked Sub",
                user_id=self.owner_id,
                organization_id=organization.id,
            )
            db.session.add_all([project, sub])
            db.session.flush()
            db.session.add(
                ProjectSubcontractor(
                    project_id=project.id,
                    subcontractor_id=sub.id,
                )
            )
            db.session.commit()

            usage = get_organization_usage(organization)
            self.assertEqual(usage.subcontractor_count, 1)

    def test_same_organization_members_share_usage(self):
        with self.app.app_context():
            self.create_projects(self.organization_id, 10)
            organization = db.session.get(Organization, self.organization_id)
            member = db.session.get(User, self.member_id)

            self.assertFalse(can_create_project(organization).allowed)
            self.assertFalse(member.paid)
            self.assertEqual(get_organization_usage(organization).project_count, 10)

    def test_tenants_have_isolated_capacity(self):
        with self.app.app_context():
            self.create_projects(self.organization_id, 10)
            organization = db.session.get(Organization, self.organization_id)
            other = db.session.get(Organization, self.other_organization_id)

            self.assertFalse(can_create_project(organization).allowed)
            self.assertTrue(can_create_project(other).allowed)

    def test_user_id_does_not_influence_capacity_count(self):
        with self.app.app_context():
            db.session.add(
                Project(
                    name="Legacy User Different Tenant",
                    user_id=self.owner_id,
                    organization_id=self.other_organization_id,
                )
            )
            db.session.commit()

            organization = db.session.get(Organization, self.organization_id)
            other = db.session.get(Organization, self.other_organization_id)

            self.assertEqual(get_organization_usage(organization).project_count, 0)
            self.assertEqual(get_organization_usage(other).project_count, 1)

    def test_user_paid_does_not_change_capacity(self):
        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            paid_user = db.session.get(User, self.owner_id)
            paid_user.paid = True
            db.session.commit()

            self.create_projects(organization.id, 10)

            self.assertFalse(can_create_project(organization).allowed)
            self.assertEqual(organization.plan_key, STARTER)

    def test_invited_member_with_legacy_unpaid_state_can_use_organization(self):
        response = self.login(
            email="member@example.com",
            password="password123",
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Risk & Mobilization Dashboard", body)
        self.assertNotIn("You need to subscribe", body)

    def test_unpaid_user_without_membership_still_uses_legacy_subscribe_gate(self):
        with self.app.app_context():
            user = User(email="unpaid-alone@example.com", paid=False)
            user.set_password("password123")
            db.session.add(user)
            db.session.commit()

        response = self.login(
            email="unpaid-alone@example.com",
            password="password123",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "You need to subscribe before accessing the platform.",
            response.get_data(as_text=True),
        )

    def test_set_organization_plan_validates_without_committing(self):
        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            set_organization_plan(organization, PROFESSIONAL)

            self.assertEqual(organization.plan_key, PROFESSIONAL)

            db.session.rollback()
            organization = db.session.get(Organization, self.organization_id)
            self.assertEqual(organization.plan_key, STARTER)

    def test_count_before_insert_allows_concurrent_checks_to_observe_capacity(self):
        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            self.create_projects(organization.id, 9)

            first_request_check = can_create_project(organization)
            second_request_check = can_create_project(organization)

            self.assertTrue(first_request_check.allowed)
            self.assertTrue(second_request_check.allowed)
            self.assertEqual(first_request_check.current, 9)
            self.assertEqual(second_request_check.current, 9)

    def test_add_project_blocks_before_insert_and_upload(self):
        self.login()

        with self.app.app_context():
            self.create_projects(self.organization_id, 10)

        token = self.csrf_token("/add_project")

        with patch(
            "app.routes.projects.save_document_file"
        ) as save_document_file:
            response = self.client.post(
                "/add_project",
                data={
                    "name": "Blocked Project",
                    "contract_value": "0",
                    "csrf_token": token,
                },
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Your Starter plan allows up to 10 projects.",
            response.get_data(as_text=True),
        )
        save_document_file.assert_not_called()

        with self.app.app_context():
            self.assertIsNone(
                Project.query.filter_by(name="Blocked Project").first()
            )

    def test_add_project_without_active_organization_fails_safely(self):
        with self.app.app_context():
            user = User(email="no-org@example.com", paid=True)
            user.set_password("password123")
            db.session.add(user)
            db.session.commit()

        self.login(email="no-org@example.com", password="password123")
        token = self.csrf_token("/login")

        response = self.client.post(
            "/add_project",
            data={
                "name": "No Org Project",
                "contract_value": "0",
                "csrf_token": token,
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_add_sub_blocks_before_insert_upload_and_analysis(self):
        self.login()

        with self.app.app_context():
            self.create_subcontractors(self.organization_id, 25)

        token = self.csrf_token("/add_sub")

        with patch(
            "app.routes.subcontractors.save_document_file"
        ) as save_document_file, patch(
            "app.routes.subcontractors.analyze_and_save_document"
        ) as analyze:
            response = self.client.post(
                "/add_sub",
                data={
                    "name": "Blocked Sub",
                    "email": "blocked@example.com",
                    "csrf_token": token,
                },
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Your Starter plan allows up to 25 subcontractors.",
            response.get_data(as_text=True),
        )
        save_document_file.assert_not_called()
        analyze.assert_not_called()

        with self.app.app_context():
            self.assertIsNone(
                Subcontractor.query.filter_by(name="Blocked Sub").first()
            )

    def test_organization_above_limit_preserves_existing_data(self):
        with self.app.app_context():
            organization = db.session.get(Organization, self.organization_id)
            self.create_projects(organization.id, 11)

            check = can_create_project(organization)

            self.assertFalse(check.allowed)
            self.assertEqual(check.current, 11)
            self.assertEqual(Project.query.filter_by(
                organization_id=organization.id
            ).count(), 11)

    def test_add_project_requires_csrf_before_capacity(self):
        self.login()
        response = self.client.post(
            "/add_project",
            data={"name": "No CSRF"},
        )
        self.assertEqual(response.status_code, 403)

    def test_route_creation_allowed_normally_under_limit(self):
        self.login()
        token = self.csrf_token("/add_project")
        response = self.client.post(
            "/add_project",
            data={
                "name": "Allowed Project",
                "contract_value": "0",
                "csrf_token": token,
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            self.assertIsNotNone(
                Project.query.filter_by(name="Allowed Project").first()
            )

    def test_session_tampering_does_not_use_other_organization_capacity(self):
        self.login()

        with self.app.app_context():
            self.create_projects(self.organization_id, 10)

        with self.client.session_transaction() as session:
            session["organization_id"] = self.other_organization_id

        token = self.csrf_token("/add_project")
        response = self.client.post(
            "/add_project",
            data={
                "name": "Tampered Project",
                "contract_value": "0",
                "csrf_token": token,
            },
            follow_redirects=True,
        )

        self.assertIn(
            "Your Starter plan allows up to 10 projects.",
            response.get_data(as_text=True),
        )


class PlanCapacityMigrationTest(unittest.TestCase):

    def tearDown(self):
        pass

    def temporary_migrated_app(self, revision="head"):
        return TemporaryMigratedApp(revision=revision)

    def test_migration_upgrade_adds_plan_key_to_empty_database(self):
        with self.temporary_migrated_app() as app:
            with app.app_context():
                inspector = inspect(db.engine)
                organization_columns = {
                    column["name"]: column
                    for column in inspector.get_columns("organization")
                }

                self.assertIn("plan_key", organization_columns)
                self.assertFalse(organization_columns["plan_key"]["nullable"])

    def test_migration_backfills_existing_organizations_to_starter(self):
        with self.temporary_migrated_app("3f2a8b6c9d10") as app:
            with app.app_context():
                db.session.execute(
                    text(
                        """
                        INSERT INTO organization
                            (id, name, created_at, updated_at)
                        VALUES
                            (5001, 'Legacy Org', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """
                    )
                )
                db.session.commit()

                upgrade(directory="migrations")

                self.assertEqual(
                    db.session.execute(
                        text(
                            """
                            SELECT plan_key
                            FROM organization
                            WHERE id = 5001
                            """
                        )
                    ).scalar_one(),
                    STARTER,
                )

    def test_migration_downgrade_removes_plan_key_without_domain_loss(self):
        with self.temporary_migrated_app() as app:
            with app.app_context():
                db.session.execute(
                    text(
                        """
                        INSERT INTO user
                            (id, email, password_hash, paid, timezone)
                        VALUES
                            (9001, 'downgrade@example.com', 'hash', 1, 'UTC')
                        """
                    )
                )
                db.session.execute(
                    text(
                        """
                        INSERT INTO organization
                            (id, name, plan_key, created_at, updated_at)
                        VALUES
                            (9002, 'Downgrade Org', 'PROFESSIONAL', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """
                    )
                )
                db.session.execute(
                    text(
                        """
                        INSERT INTO organization_membership
                            (organization_id, user_id, role, created_at)
                        VALUES
                            (9002, 9001, 'OWNER', CURRENT_TIMESTAMP)
                        """
                    )
                )
                db.session.commit()

                downgrade(directory="migrations", revision="3f2a8b6c9d10")
                inspector = inspect(db.engine)
                organization_columns = {
                    column["name"]
                    for column in inspector.get_columns("organization")
                }

                self.assertNotIn("plan_key", organization_columns)
                self.assertEqual(
                    db.session.execute(
                        text("SELECT COUNT(*) FROM organization")
                    ).scalar_one(),
                    1,
                )
                self.assertEqual(
                    db.session.execute(
                        text("SELECT COUNT(*) FROM organization_membership")
                    ).scalar_one(),
                    1,
                )


class TemporaryMigratedApp:

    def __init__(self, revision="head"):
        self.revision = revision

    def __enter__(self):
        self.database = tempfile.NamedTemporaryFile(
            suffix=".sqlite",
            delete=False,
        )
        self.database.close()

        database_uri = "sqlite:///" + self.database.name.replace("\\", "/")

        class TempMigrationConfig(TestingConfig):
            SQLALCHEMY_DATABASE_URI = database_uri

        self.app = create_app(TempMigrationConfig)

        with self.app.app_context():
            upgrade(
                directory="migrations",
                revision=self.revision,
            )

        return self.app

    def __exit__(self, exc_type, exc, tb):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

        try:
            os.unlink(self.database.name)
        except OSError:
            pass


if __name__ == "__main__":
    unittest.main()
