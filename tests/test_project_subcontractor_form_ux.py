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
from app.models import Document, Project, ProjectSubcontractor, Subcontractor, User
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

    def add_valid_coi_document(self, coverage):
        document = Document(
            filename="coi.pdf",
            original_name="coi.pdf",
            document_type="COI",
            sub_id=self.subcontractor_id,
            uploaded_by=self.owner_id,
            ai_status="analyzed",
            ai_confidence=0.95,
            ai_extracted_data={
                "expiration_date": (
                    date.today() + timedelta(days=60)
                ).isoformat(),
                "general_liability_each_occurrence": coverage,
                "general_liability_limit": coverage,
                "confidence": 0.95,
            },
            ai_compliance_result={
                "status": "Ready",
                "issues": [],
                "warnings": [],
                "confidence": 0.95,
            },
        )
        subcontractor = db.session.get(
            Subcontractor,
            self.subcontractor_id,
        )
        subcontractor.documents.append(document)
        db.session.flush()
        return document

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
        self.assertIn('id="required-coverage-custom-group"', body)
        self.assertNotIn("Contract Value", body)
        self.assertNotIn("Start Date", body)
        self.assertNotIn("projects currently in this Organization", body)

    def test_add_project_does_not_require_contract_value_or_start_date(self):
        self.login()
        token = self.csrf_token("/add_project")

        response = self.client.post(
            "/add_project",
            data={
                "csrf_token": token,
                "name": "Minimal Project",
                "required_coverage_choice": "",
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            project = Project.query.filter_by(name="Minimal Project").one()
            self.assertEqual(project.contract_value, 0)
            self.assertIsNone(project.start_date)
            self.assertIsNone(project.end_date)

    def test_add_project_ignores_removed_contract_value_and_start_date_fields(self):
        self.login()
        token = self.csrf_token("/add_project")

        response = self.client.post(
            "/add_project",
            data={
                "csrf_token": token,
                "name": "Manipulated Minimal Project",
                "contract_value": "999999",
                "start_date": "2026-01-01",
                "required_coverage_choice": "1000000",
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            project = Project.query.filter_by(
                name="Manipulated Minimal Project"
            ).one()
            self.assertEqual(project.contract_value, 0)
            self.assertIsNone(project.start_date)
            self.assertEqual(project.required_coverage, 1000000)

    def test_add_project_still_requires_project_name(self):
        self.login()
        token = self.csrf_token("/add_project")

        response = self.client.post(
            "/add_project",
            data={
                "csrf_token": token,
                "name": "",
                "required_coverage_choice": "",
            },
        )

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Project name is required.", body)

    def test_add_project_end_date_is_optional_and_can_be_saved(self):
        self.login()
        token = self.csrf_token("/add_project")

        response = self.client.post(
            "/add_project",
            data={
                "csrf_token": token,
                "name": "End Date Project",
                "end_date": "2027-06-30",
                "required_coverage_choice": "",
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            project = Project.query.filter_by(name="End Date Project").one()
            self.assertEqual(project.end_date.isoformat(), "2027-06-30")

    def test_add_project_without_subcontractors_is_allowed(self):
        self.login()
        token = self.csrf_token("/add_project")

        response = self.client.post(
            "/add_project",
            data={
                "csrf_token": token,
                "name": "Empty Project",
                "required_coverage_choice": "2000000",
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            project = Project.query.filter_by(name="Empty Project").one()
            self.assertEqual(
                ProjectSubcontractor.query.filter_by(
                    project_id=project.id
                ).count(),
                0,
            )

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

    def test_edit_project_shows_only_final_editable_fields(self):
        with self.app.app_context():
            project = Project(
                name="Focused Edit",
                contract_value=7500000,
                user_id=self.owner_id,
                organization_id=self.organization_id,
                start_date=date(2026, 1, 1),
                end_date=date(2027, 1, 1),
                required_coverage=5000000,
            )
            db.session.add(project)
            db.session.flush()
            document = Document(
                filename="projects/focused/contract.pdf",
                original_name="contract.pdf",
                document_type="Contract",
                version=1,
                project_id=project.id,
                uploaded_by=self.owner_id,
            )
            db.session.add(document)
            db.session.commit()
            project_id = project.id

        self.login()
        response = self.client.get(f"/edit_project/{project_id}")
        body = response.get_data(as_text=True)

        self.assertIn("Project Name", body)
        self.assertIn("Project End Date", body)
        self.assertIn("Minimum General Liability Coverage", body)
        self.assertIn("Assigned Subcontractors", body)
        self.assertIn("Select the subcontractors working on this project.", body)
        self.assertIn("Project Requirements", body)
        self.assertIn("1 document on file", body)
        self.assertIn("Contract", body)
        self.assertIn("View Documents", body)
        self.assertIn('id="required-coverage-custom-row" hidden', body)

        self.assertNotIn("Contract Value", body)
        self.assertNotIn("Start Date", body)
        self.assertNotIn("Contract Status", body)
        self.assertNotIn("Days Remaining", body)
        self.assertNotIn("Initial Documents", body)
        self.assertNotIn("Project Document Type", body)
        self.assertNotIn("Upload Project Document", body)
        self.assertNotIn("AI confidence", body)

    def test_edit_project_shows_custom_amount_only_for_custom_coverage(self):
        with self.app.app_context():
            preset_project = Project(
                name="Preset GL",
                contract_value=0,
                user_id=self.owner_id,
                organization_id=self.organization_id,
                required_coverage=5000000,
            )
            custom_project = Project(
                name="Custom GL",
                contract_value=0,
                user_id=self.owner_id,
                organization_id=self.organization_id,
                required_coverage=3000000,
            )
            db.session.add_all([preset_project, custom_project])
            db.session.commit()
            preset_project_id = preset_project.id
            custom_project_id = custom_project.id

        self.login()

        preset_response = self.client.get(f"/edit_project/{preset_project_id}")
        preset_body = preset_response.get_data(as_text=True)
        self.assertIn('id="required-coverage-custom-row" hidden', preset_body)

        custom_response = self.client.get(f"/edit_project/{custom_project_id}")
        custom_body = custom_response.get_data(as_text=True)
        self.assertIn('id="required-coverage-custom-row"', custom_body)
        self.assertNotIn('id="required-coverage-custom-row" hidden', custom_body)
        self.assertIn('value="3000000"', custom_body)

    def test_edit_project_ignores_removed_fields_from_manipulated_post(self):
        original_start = date(2026, 1, 15)
        original_contract_value = 4250000
        with self.app.app_context():
            project = Project(
                name="Hardened Edit",
                contract_value=original_contract_value,
                user_id=self.owner_id,
                organization_id=self.organization_id,
                start_date=original_start,
                end_date=date(2026, 12, 31),
                required_coverage=1000000,
            )
            db.session.add(project)
            db.session.commit()
            project_id = project.id

        self.login()
        token = self.csrf_token(f"/edit_project/{project_id}")
        response = self.client.post(
            f"/edit_project/{project_id}",
            data={
                "csrf_token": token,
                "name": "Hardened Edit Updated",
                "contract_value": "not-a-number",
                "start_date": "not-a-date",
                "end_date": "2027-02-01",
                "required_coverage_choice": "2000000",
                "required_coverage_custom": "5000000",
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            project = db.session.get(Project, project_id)
            self.assertEqual(project.name, "Hardened Edit Updated")
            self.assertEqual(project.contract_value, original_contract_value)
            self.assertEqual(project.start_date, original_start)
            self.assertEqual(project.end_date, date(2027, 2, 1))
            self.assertEqual(project.required_coverage, 2000000)

    def test_edit_project_rejects_invalid_end_date_and_custom_coverage(self):
        with self.app.app_context():
            project = Project(
                name="Reject Invalid",
                contract_value=0,
                user_id=self.owner_id,
                organization_id=self.organization_id,
                required_coverage=1000000,
            )
            db.session.add(project)
            db.session.commit()
            project_id = project.id

        self.login()

        token = self.csrf_token(f"/edit_project/{project_id}")
        invalid_end_response = self.client.post(
            f"/edit_project/{project_id}",
            data={
                "csrf_token": token,
                "name": "Reject Invalid",
                "end_date": "not-a-date",
                "required_coverage_choice": "1000000",
            },
        )
        self.assertEqual(invalid_end_response.status_code, 200)
        self.assertIn("Invalid date format.", invalid_end_response.get_data(as_text=True))

        token = self.csrf_token(f"/edit_project/{project_id}")
        invalid_custom_response = self.client.post(
            f"/edit_project/{project_id}",
            data={
                "csrf_token": token,
                "name": "Reject Invalid",
                "required_coverage_choice": "custom",
                "required_coverage_custom": "-1",
            },
        )
        self.assertEqual(invalid_custom_response.status_code, 200)
        self.assertIn(
            "Invalid minimum coverage amount.",
            invalid_custom_response.get_data(as_text=True),
        )

    def test_edit_project_updates_assigned_subcontractors_with_org_isolation(self):
        with self.app.app_context():
            project = Project(
                name="Assignments",
                contract_value=0,
                user_id=self.owner_id,
                organization_id=self.organization_id,
                required_coverage=None,
            )
            db.session.add(project)
            db.session.commit()
            project_id = project.id

        self.login()
        token = self.csrf_token(f"/edit_project/{project_id}")
        response = self.client.post(
            f"/edit_project/{project_id}",
            data={
                "csrf_token": token,
                "name": "Assignments",
                "required_coverage_choice": "",
                "subcontractors": [
                    str(self.subcontractor_id),
                    str(self.other_subcontractor_id),
                ],
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            links = ProjectSubcontractor.query.filter_by(
                project_id=project_id
            ).all()
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0].subcontractor_id, self.subcontractor_id)

        token = self.csrf_token(f"/edit_project/{project_id}")
        response = self.client.post(
            f"/edit_project/{project_id}",
            data={
                "csrf_token": token,
                "name": "Assignments",
                "required_coverage_choice": "",
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            links = ProjectSubcontractor.query.filter_by(
                project_id=project_id
            ).all()
            self.assertEqual(links, [])

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
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            subcontractor = Subcontractor.query.filter_by(
                name="No COI Sub"
            ).one()
            self.assertIsNone(subcontractor.coi_expiration)
            self.assertEqual(subcontractor.timezone, "US/Eastern")

        response = self.client.get("/add_sub")
        body = response.get_data(as_text=True)
        self.assertIn("Email is used for COI requests.", body)
        self.assertIn("COI (optional)", body)
        self.assertIn("BuildSure analyzes supported COIs automatically", body)
        self.assertNotIn("Phone", body)
        self.assertNotIn("Timezone", body)
        self.assertNotIn("COI Expiration", body)
        self.assertNotIn("subcontractors currently in this Organization", body)

    def test_add_sub_ignores_removed_coi_date_phone_and_timezone_fields(self):
        self.login()
        token = self.csrf_token("/add_sub")

        response = self.client.post(
            "/add_sub",
            data={
                "csrf_token": token,
                "name": "Manipulated Sub",
                "email": "manipulated@example.com",
                "phone": "555-1111",
                "coi_expiration": "07/15/2026",
                "timezone": "Not/AZone",
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            subcontractor = Subcontractor.query.filter_by(
                name="Manipulated Sub"
            ).one()
            self.assertIsNone(subcontractor.phone)
            self.assertIsNone(subcontractor.coi_expiration)
            self.assertEqual(subcontractor.timezone, "US/Eastern")

    def test_add_sub_does_not_require_phone_timezone_or_manual_coi_expiration(self):
        self.login()
        token = self.csrf_token("/add_sub")

        response = self.client.post(
            "/add_sub",
            data={
                "csrf_token": token,
                "name": "Minimal Sub",
                "email": "",
                "role": "Concrete",
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            subcontractor = Subcontractor.query.filter_by(
                name="Minimal Sub"
            ).one()
            self.assertEqual(subcontractor.email, "")
            self.assertEqual(subcontractor.role, "Concrete")
            self.assertIsNone(subcontractor.phone)
            self.assertIsNone(subcontractor.coi_expiration)

    def test_edit_sub_renders_simplified_company_projects_and_coi_summary(self):
        self.login()

        response = self.client.get(f"/edit_sub/{self.subcontractor_id}")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Company Name", body)
        self.assertIn("Email", body)
        self.assertIn("Used for COI requests.", body)
        self.assertIn("Trade", body)
        self.assertIn("Projects", body)
        self.assertIn("Current COI", body)
        self.assertIn("View Documents", body)
        self.assertIn("ux-btn ux-btn-secondary", body)
        self.assertNotIn(">Cancel<", body)
        self.assertNotIn('name="phone"', body)
        self.assertNotIn('name="timezone"', body)
        self.assertNotIn('name="coi_expiration"', body)
        self.assertNotIn("How it works", body)
        self.assertNotIn("Upload Documents", body)
        self.assertNotIn("Document Type", body)
        self.assertNotIn("Coverage is evaluated from validated COI evidence", body)
        self.assertNotIn(">READY<", body)
        self.assertNotIn(">BLOCKED<", body)

    def test_edit_sub_valid_coi_summary_uses_validated_evidence_coverage(self):
        with self.app.app_context():
            self.add_valid_coi_document(2_000_000)
            db.session.commit()

        self.login()
        response = self.client.get(f"/edit_sub/{self.subcontractor_id}")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("VALID", body)
        self.assertIn("Expires", body)
        self.assertIn("GL Coverage: $2M", body)

    def test_edit_sub_expired_missing_and_checking_coi_summary(self):
        with self.app.app_context():
            missing_sub = Subcontractor(
                name="Missing Summary Sub",
                user_id=self.owner_id,
                organization_id=self.organization_id,
            )
            expired_sub = Subcontractor(
                name="Expired Summary Sub",
                coi_expiration=date.today() - timedelta(days=1),
                user_id=self.owner_id,
                organization_id=self.organization_id,
            )
            checking_sub = Subcontractor(
                name="Checking Summary Sub",
                user_id=self.owner_id,
                organization_id=self.organization_id,
            )
            db.session.add_all([missing_sub, expired_sub, checking_sub])
            db.session.flush()
            db.session.add(
                Document(
                    filename="checking.pdf",
                    original_name="checking.pdf",
                    document_type="COI",
                    sub_id=checking_sub.id,
                    uploaded_by=self.owner_id,
                    ai_status="not_analyzed",
                )
            )
            db.session.commit()
            missing_id = missing_sub.id
            expired_id = expired_sub.id
            checking_id = checking_sub.id

        self.login()
        missing_body = self.client.get(
            f"/edit_sub/{missing_id}"
        ).get_data(as_text=True)
        expired_body = self.client.get(
            f"/edit_sub/{expired_id}"
        ).get_data(as_text=True)
        checking_body = self.client.get(
            f"/edit_sub/{checking_id}"
        ).get_data(as_text=True)

        self.assertIn("No COI on file.", missing_body)
        self.assertIn("Required to send COI requests.", missing_body)
        self.assertIn("EXPIRED", expired_body)
        self.assertIn("Expired", expired_body)
        self.assertIn("CHECKING", checking_body)
        self.assertIn(
            "BuildSure is processing the latest COI.",
            checking_body,
        )

    def test_edit_sub_hides_unvalidated_coverage(self):
        with self.app.app_context():
            sub = db.session.get(Subcontractor, self.subcontractor_id)
            document = Document(
                filename="unvalidated.pdf",
                original_name="unvalidated.pdf",
                document_type="COI",
                sub_id=self.subcontractor_id,
                uploaded_by=self.owner_id,
                ai_status="analyzed",
                ai_confidence=0.2,
                ai_extracted_data={
                    "expiration_date": (
                        date.today() + timedelta(days=60)
                    ).isoformat(),
                    "coverage_limit": 5_000_000,
                    "confidence": 0.2,
                },
                ai_compliance_result={
                    "status": "Ready",
                    "issues": [],
                    "warnings": [],
                    "confidence": 0.2,
                },
            )
            sub.documents.append(document)
            db.session.commit()

        self.login()
        body = self.client.get(
            f"/edit_sub/{self.subcontractor_id}"
        ).get_data(as_text=True)

        self.assertIn("VALID", body)
        self.assertNotIn("GL Coverage", body)

    def test_edit_sub_manipulated_post_preserves_hidden_fields(self):
        with self.app.app_context():
            subcontractor = db.session.get(
                Subcontractor,
                self.subcontractor_id,
            )
            subcontractor.phone = "555-0000"
            subcontractor.timezone = "US/Central"
            subcontractor.coi_expiration = date.today() + timedelta(days=60)
            db.session.commit()
            original_expiration = subcontractor.coi_expiration

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
                "timezone": "Not/AZone",
                "coi_expiration": "",
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            subcontractor = db.session.get(
                Subcontractor,
                self.subcontractor_id,
            )
            self.assertEqual(subcontractor.phone, "555-0000")
            self.assertEqual(subcontractor.timezone, "US/Central")
            self.assertEqual(subcontractor.coi_expiration, original_expiration)
            self.assertEqual(subcontractor.name, "Owned Sub Updated")
            self.assertEqual(subcontractor.role, "Concrete")

    def test_edit_sub_project_assignments_keep_organization_isolation(self):
        with self.app.app_context():
            owned_project = Project(
                name="Owned Assignment",
                user_id=self.owner_id,
                organization_id=self.organization_id,
            )
            other_project = Project(
                name="Other Assignment",
                user_id=self.other_id,
                organization_id=self.other_organization_id,
            )
            db.session.add_all([owned_project, other_project])
            db.session.commit()
            owned_project_id = owned_project.id
            other_project_id = other_project.id

        self.login()
        token = self.csrf_token(f"/edit_sub/{self.subcontractor_id}")

        response = self.client.post(
            f"/edit_sub/{self.subcontractor_id}",
            data={
                "csrf_token": token,
                "name": "Owned Sub",
                "email": "owned@example.com",
                "role": "Concrete",
                "projects": [
                    str(owned_project_id),
                    str(other_project_id),
                    "not-an-id",
                ],
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            links = ProjectSubcontractor.query.filter_by(
                subcontractor_id=self.subcontractor_id,
            ).all()
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0].project_id, owned_project_id)

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
            )
            db.session.add(link)
            db.session.flush()

            self.add_valid_coi_document(1000000)
            blocked = calculate_readiness(link)
            self.assertEqual(blocked["status"], BLOCKED)

            self.add_valid_coi_document(2000000)
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
            )
            zero_link = ProjectSubcontractor(
                project_id=zero_project.id,
                subcontractor_id=self.subcontractor_id,
            )
            five_million_link = ProjectSubcontractor(
                project_id=five_million_project.id,
                subcontractor_id=self.subcontractor_id,
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

            self.add_valid_coi_document(2000000)
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
        self.assertIn("Project Requirements (optional)", project_body)
        self.assertIn("Assigned Subcontractors", project_body)
        self.assertIn("aria-describedby", project_body)
        self.assertNotIn("calculate_readiness", project_body)

        sub_response = self.client.get("/add_sub")
        sub_body = sub_response.get_data(as_text=True)
        self.assertIn("Company Information", sub_body)
        self.assertIn("COI (optional)", sub_body)
        self.assertIn("BuildSure analyzes supported COIs automatically", sub_body)
        self.assertNotIn("Insurance Information", sub_body)
        self.assertNotIn("Settings", sub_body)
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
