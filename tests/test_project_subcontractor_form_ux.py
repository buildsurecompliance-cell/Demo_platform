import os
import re
import tempfile
import unittest

from datetime import date, timedelta

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Project, ProjectSubcontractor, Subcontractor, User
from app.services.organizations import create_default_organization_for_user
from app.services.readiness_service import BLOCKED, READY, calculate_readiness


class ProjectSubcontractorFormUXTest(unittest.TestCase):

    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory()
        self.app = create_app(TestingConfig)
        self.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=True,
            UPLOAD_FOLDER=self.uploads.name,
            RATELIMIT_ENABLED=False,
        )
        self.client = self.app.test_client()

        with self.app.app_context():
            db.drop_all()
            db.create_all()

            self.owner = User(email="owner@example.com", paid=True)
            self.owner.set_password("password123")
            self.other = User(email="other@example.com", paid=True)
            self.other.set_password("password123")
            db.session.add_all([self.owner, self.other])
            db.session.flush()

            self.organization = create_default_organization_for_user(
                self.owner
            )
            self.other_organization = create_default_organization_for_user(
                self.other
            )

            self.subcontractor = Subcontractor(
                name="Owned Sub",
                email="owned@example.com",
                user_id=self.owner.id,
                organization_id=self.organization.id,
                coi_expiration=date.today() + timedelta(days=60),
            )
            self.other_subcontractor = Subcontractor(
                name="Other Sub",
                email="other-sub@example.com",
                user_id=self.other.id,
                organization_id=self.other_organization.id,
            )
            db.session.add_all(
                [
                    self.subcontractor,
                    self.other_subcontractor,
                ]
            )
            db.session.commit()

            self.owner_id = self.owner.id
            self.other_id = self.other.id
            self.organization_id = self.organization.id
            self.other_organization_id = self.other_organization.id
            self.subcontractor_id = self.subcontractor.id
            self.other_subcontractor_id = self.other_subcontractor.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

        self.uploads.cleanup()

    def login(self, user_id=None, organization_id=None):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id or self.owner_id)
            session["_fresh"] = True
            session["organization_id"] = organization_id or self.organization_id

    def csrf_token(self, path):
        response = self.client.get(path)
        match = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            response.data,
        )
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def test_project_form_shows_required_coverage_options(self):
        self.login()

        response = self.client.get("/add_project")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Minimum General Liability Coverage", body)
        self.assertIn("No minimum", body)
        self.assertIn("$1M", body)
        self.assertIn("$2M", body)
        self.assertIn("$5M", body)
        self.assertIn("Custom amount", body)

    def test_add_project_persists_required_coverage_and_owned_links_only(self):
        self.login()
        token = self.csrf_token("/add_project")

        response = self.client.post(
            "/add_project",
            data={
                "csrf_token": token,
                "name": "Coverage Project",
                "contract_value": "1000",
                "required_coverage_choice": "2000000",
                "subcontractors": [
                    str(self.subcontractor_id),
                    str(self.other_subcontractor_id),
                    "not-an-id",
                ],
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            project = Project.query.filter_by(
                name="Coverage Project"
            ).one()
            self.assertEqual(project.required_coverage, 2000000)
            links = ProjectSubcontractor.query.filter_by(
                project_id=project.id
            ).all()
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0].subcontractor_id, self.subcontractor_id)

    def test_add_project_rejects_invalid_required_coverage(self):
        self.login()
        token = self.csrf_token("/add_project")

        response = self.client.post(
            "/add_project",
            data={
                "csrf_token": token,
                "name": "Invalid Coverage Project",
                "contract_value": "1000",
                "required_coverage_choice": "custom",
                "required_coverage_custom": "-1",
            },
        )

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Invalid minimum coverage amount.", body)

        with self.app.app_context():
            self.assertIsNone(
                Project.query.filter_by(
                    name="Invalid Coverage Project"
                ).first()
            )

    def test_required_coverage_accepts_presets_empty_zero_and_custom(self):
        self.login()

        cases = [
            ("No Minimum", "", "", None),
            ("Zero Minimum", "custom", "0", None),
            ("Preset 1M", "1000000", "", 1000000),
            ("Preset 2M", "2000000", "", 2000000),
            ("Preset 5M", "5000000", "", 5000000),
            ("Custom", "custom", "3000000", 3000000),
            ("High Custom", "custom", "999999999", 999999999),
        ]

        for name, choice, custom, expected in cases:
            with self.subTest(name=name):
                token = self.csrf_token("/add_project")
                response = self.client.post(
                    "/add_project",
                    data={
                        "csrf_token": token,
                        "name": name,
                        "contract_value": "0",
                        "required_coverage_choice": choice,
                        "required_coverage_custom": custom,
                    },
                )

                self.assertEqual(response.status_code, 302)

                with self.app.app_context():
                    project = Project.query.filter_by(name=name).one()
                    self.assertEqual(project.required_coverage, expected)

    def test_required_coverage_rejects_text_negative_decimal_and_bad_choice(self):
        self.login()

        cases = [
            ("Text Coverage", "custom", "abc"),
            ("Negative Coverage", "custom", "-10"),
            ("Decimal Coverage", "custom", "1000000.50"),
            ("Bad Choice", "999999", ""),
        ]

        for name, choice, custom in cases:
            with self.subTest(name=name):
                token = self.csrf_token("/add_project")
                response = self.client.post(
                    "/add_project",
                    data={
                        "csrf_token": token,
                        "name": name,
                        "contract_value": "0",
                        "required_coverage_choice": choice,
                        "required_coverage_custom": custom,
                    },
                )

                body = response.get_data(as_text=True)
                self.assertEqual(response.status_code, 200)
                self.assertIn("Invalid minimum coverage amount.", body)
                self.assertIn(name, body)

                with self.app.app_context():
                    self.assertIsNone(Project.query.filter_by(name=name).first())

    def test_custom_coverage_ignored_when_preset_selected(self):
        self.login()
        token = self.csrf_token("/add_project")

        response = self.client.post(
            "/add_project",
            data={
                "csrf_token": token,
                "name": "Preset Ignores Custom",
                "contract_value": "0",
                "required_coverage_choice": "1000000",
                "required_coverage_custom": "5000000",
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            project = Project.query.filter_by(
                name="Preset Ignores Custom"
            ).one()
            self.assertEqual(project.required_coverage, 1000000)

    def test_edit_project_preserves_and_updates_required_coverage(self):
        with self.app.app_context():
            project = Project(
                name="Editable",
                contract_value=0,
                user_id=self.owner_id,
                organization_id=self.organization_id,
                required_coverage=1000000,
            )
            db.session.add(project)
            db.session.commit()
            project_id = project.id

        self.login()
        response = self.client.get(f"/edit_project/{project_id}")
        self.assertIn('value="1000000" selected', response.get_data(as_text=True))

        token = self.csrf_token(f"/edit_project/{project_id}")
        response = self.client.post(
            f"/edit_project/{project_id}",
            data={
                "csrf_token": token,
                "name": "Editable",
                "contract_value": "0",
                "required_coverage_choice": "custom",
                "required_coverage_custom": "3000000",
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            project = db.session.get(Project, project_id)
            self.assertEqual(project.required_coverage, 3000000)

    def test_other_organization_project_cannot_be_edited(self):
        with self.app.app_context():
            other_project = Project(
                name="Other Project",
                contract_value=0,
                user_id=self.other_id,
                organization_id=self.other_organization_id,
                required_coverage=1000000,
            )
            db.session.add(other_project)
            db.session.commit()
            project_id = other_project.id

        self.login()

        response = self.client.get(f"/edit_project/{project_id}")

        self.assertEqual(response.status_code, 404)

    def test_add_sub_allows_missing_coi_date_and_shows_extraction_help(self):
        self.login()
        token = self.csrf_token("/add_sub")

        response = self.client.post(
            "/add_sub",
            data={
                "csrf_token": token,
                "name": "No COI Sub",
                "email": "nocoi@example.com",
                "timezone": "America/New_York",
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            subcontractor = Subcontractor.query.filter_by(
                name="No COI Sub"
            ).one()
            self.assertIsNone(subcontractor.coi_expiration)
            self.assertEqual(subcontractor.timezone, "America/New_York")

        response = self.client.get("/add_sub")
        self.assertIn(
            "BuildSure can extract the expiration date automatically.",
            response.get_data(as_text=True),
        )

    def test_add_sub_rejects_invalid_coi_date_before_insert(self):
        self.login()
        token = self.csrf_token("/add_sub")

        response = self.client.post(
            "/add_sub",
            data={
                "csrf_token": token,
                "name": "Bad Date Sub",
                "email": "bad-date@example.com",
                "coi_expiration": "07/15/2026",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Invalid date format.", response.get_data(as_text=True))

        with self.app.app_context():
            self.assertIsNone(
                Subcontractor.query.filter_by(name="Bad Date Sub").first()
            )

    def test_add_sub_accepts_valid_coi_date_and_rejects_invalid_timezone(self):
        self.login()
        token = self.csrf_token("/add_sub")

        response = self.client.post(
            "/add_sub",
            data={
                "csrf_token": token,
                "name": "Dated Sub",
                "email": "dated@example.com",
                "coi_expiration": "2026-12-31",
                "timezone": "America/Chicago",
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            subcontractor = Subcontractor.query.filter_by(
                name="Dated Sub"
            ).one()
            self.assertEqual(subcontractor.coi_expiration.isoformat(), "2026-12-31")
            self.assertEqual(subcontractor.timezone, "America/Chicago")

        token = self.csrf_token("/add_sub")
        response = self.client.post(
            "/add_sub",
            data={
                "csrf_token": token,
                "name": "Bad Timezone",
                "email": "bad-timezone@example.com",
                "timezone": "Not/AZone",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Invalid timezone.", response.get_data(as_text=True))

        with self.app.app_context():
            self.assertIsNone(
                Subcontractor.query.filter_by(name="Bad Timezone").first()
            )

    def test_edit_sub_preserves_and_clears_optional_coi_date(self):
        self.login()
        token = self.csrf_token(f"/edit_sub/{self.subcontractor_id}")

        response = self.client.post(
            f"/edit_sub/{self.subcontractor_id}",
            data={
                "csrf_token": token,
                "name": "Owned Sub Updated",
                "email": "owned@example.com",
                "phone": "555-1111",
                "role": "Concrete",
                "timezone": "US/Central",
                "coi_expiration": "",
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            subcontractor = db.session.get(
                Subcontractor,
                self.subcontractor_id,
            )
            self.assertIsNone(subcontractor.coi_expiration)
            self.assertEqual(subcontractor.timezone, "US/Central")

    def test_project_required_coverage_drives_existing_readiness_rule(self):
        with self.app.app_context():
            project = Project(
                name="Readiness Coverage",
                user_id=self.owner_id,
                organization_id=self.organization_id,
                required_coverage=2000000,
            )
            db.session.add(project)
            db.session.flush()
            link = ProjectSubcontractor(
                project_id=project.id,
                subcontractor_id=self.subcontractor_id,
                coverage_limit=1000000,
            )
            db.session.add(link)
            db.session.flush()

            blocked = calculate_readiness(link)
            self.assertEqual(blocked["status"], BLOCKED)

            link.coverage_limit = 2000000
            db.session.flush()
            ready = calculate_readiness(link)
            self.assertEqual(ready["status"], READY)

    def test_readiness_coverage_boundaries(self):
        with self.app.app_context():
            no_minimum_project = Project(
                name="No Minimum",
                user_id=self.owner_id,
                organization_id=self.organization_id,
                required_coverage=None,
            )
            zero_project = Project(
                name="Zero Minimum",
                user_id=self.owner_id,
                organization_id=self.organization_id,
                required_coverage=0,
            )
            five_million_project = Project(
                name="Five Million",
                user_id=self.owner_id,
                organization_id=self.organization_id,
                required_coverage=5000000,
            )
            db.session.add_all(
                [
                    no_minimum_project,
                    zero_project,
                    five_million_project,
                ]
            )
            db.session.flush()

            no_minimum_link = ProjectSubcontractor(
                project_id=no_minimum_project.id,
                subcontractor_id=self.subcontractor_id,
                coverage_limit=1000000,
            )
            zero_link = ProjectSubcontractor(
                project_id=zero_project.id,
                subcontractor_id=self.subcontractor_id,
                coverage_limit=1000000,
            )
            five_million_link = ProjectSubcontractor(
                project_id=five_million_project.id,
                subcontractor_id=self.subcontractor_id,
                coverage_limit=2000000,
            )
            db.session.add_all(
                [
                    no_minimum_link,
                    zero_link,
                    five_million_link,
                ]
            )
            db.session.flush()

            self.assertEqual(calculate_readiness(no_minimum_link)["status"], READY)
            self.assertEqual(calculate_readiness(zero_link)["status"], READY)

            five_million = calculate_readiness(five_million_link)
            self.assertEqual(five_million["status"], BLOCKED)
            self.assertIn(
                "COVERAGE_INSUFFICIENT",
                {reason["code"] for reason in five_million["reasons"]},
            )

    def test_templates_render_expected_sections_and_safe_content(self):
        self.login()

        project_response = self.client.get("/add_project")
        project_body = project_response.get_data(as_text=True)
        self.assertIn("Project Information", project_body)
        self.assertIn("Insurance Requirements", project_body)
        self.assertIn("Initial Documents", project_body)
        self.assertIn("Assigned Subcontractors", project_body)
        self.assertIn("aria-describedby", project_body)
        self.assertNotIn("calculate_readiness", project_body)

        sub_response = self.client.get("/add_sub")
        sub_body = sub_response.get_data(as_text=True)
        self.assertIn("Company Information", sub_body)
        self.assertIn("Insurance Information", sub_body)
        self.assertIn("How it works", sub_body)
        self.assertIn("Settings", sub_body)
        self.assertIn("aria-describedby", sub_body)
        self.assertNotIn("generate_compliance_advice", sub_body)

    def test_project_and_sub_forms_include_csrf_tokens(self):
        self.login()

        self.assertIn(
            'name="csrf_token"',
            self.client.get("/add_project").get_data(as_text=True),
        )
        self.assertIn(
            'name="csrf_token"',
            self.client.get("/add_sub").get_data(as_text=True),
        )

    def test_post_without_csrf_is_rejected(self):
        self.login()

        response = self.client.post(
            "/add_project",
            data={
                "name": "No CSRF",
                "required_coverage_choice": "1000000",
            },
        )

        self.assertEqual(response.status_code, 403)
