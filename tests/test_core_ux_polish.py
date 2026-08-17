import os
import unittest

from datetime import date, timedelta


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.extensions import db
from app.models import Document, Project, ProjectSubcontractor, Subcontractor, User
from app.services.organizations import create_default_organization_for_user


class CoreUXPolishTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
        )
        self.client = self.app.test_client()

        with self.app.app_context():
            db.drop_all()
            db.create_all()

            self.user = User(email="ux-owner@example.com", paid=True)
            self.user.set_password("password123")
            db.session.add(self.user)
            db.session.flush()
            self.organization = create_default_organization_for_user(self.user)
            db.session.commit()

            self.user_id = self.user.id
            self.organization_id = self.organization.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def login(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True
            session["organization_id"] = self.organization_id

    def create_project_subcontractor(self):
        with self.app.app_context():
            project = Project(
                name="Riverside Office Building",
                contract_value=4_850_000,
                required_coverage=2_000_000,
                start_date=date(2026, 8, 1),
                end_date=date.today() + timedelta(days=90),
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            sub = Subcontractor(
                name="Ace Concrete",
                role="Concrete",
                coi_expiration=date.today() + timedelta(days=60),
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            db.session.add_all([project, sub])
            db.session.flush()
            link = ProjectSubcontractor(
                project_id=project.id,
                subcontractor_id=sub.id,
                coverage_limit=1_000_000,
            )
            doc = Document(
                filename="subcontractors/1/coi.pdf",
                original_name="coi.pdf",
                document_type="COI",
                sub_id=sub.id,
                uploaded_by=self.user_id,
                ai_status="analyzed",
                ai_confidence=0.95,
                ai_extracted_data={
                    "expiration_date": "2029-01-01",
                    "general_liability_limit": 5_000_000,
                    "coverage_limit": 5_000_000,
                    "general_liability": {
                        "each_occurrence": 5_000_000,
                        "general_aggregate": 10_000_000,
                        "products_completed_operations": 10_000_000,
                        "expiration_date": "2029-01-01",
                    },
                    "confidence": 0.95,
                },
                ai_compliance_result={
                    "status": "Ready",
                    "score": 95,
                    "issues": [],
                    "warnings": [],
                },
            )
            contract = Document(
                filename="projects/1/contract.pdf",
                original_name="contract.pdf",
                document_type="Contract",
                project_id=project.id,
                uploaded_by=self.user_id,
                ai_status="analyzed",
                ai_confidence=0.95,
                ai_extracted_data={
                    "project_name": "Riverside Office Building",
                    "contract_value": 4_850_000,
                    "start_date": "2026-08-01",
                    "end_date": "2027-06-30",
                    "required_coverage": 2_000_000,
                    "confidence": 0.95,
                },
                ai_compliance_result={
                    "status": "Ready",
                    "project_auto_fill": {
                        "fields": {
                            "project_name": {"status": "preserved"},
                            "contract_value": {"status": "applied"},
                            "start_date": {"status": "applied"},
                            "end_date": {"status": "applied"},
                            "required_coverage": {"status": "applied"},
                        }
                    },
                },
            )
            scope = Document(
                filename="projects/1/scope.pdf",
                original_name="scope.pdf",
                document_type="Scope",
                project_id=project.id,
                uploaded_by=self.user_id,
                ai_status="failed",
            )
            db.session.add_all([link, doc, contract, scope])
            db.session.commit()
            return project.id, sub.id

    def test_dashboard_core_ux_sections_render(self):
        self.create_project_subcontractor()
        self.login()

        response = self.client.get("/dashboard")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Compliance Dashboard", body)
        self.assertIn("Active Projects", body)
        self.assertIn("Ready", body)
        self.assertIn("Checking", body)
        self.assertIn("Needs Attention", body)
        self.assertIn("Blocked", body)
        self.assertNotIn("Plan Capacity", body)
        self.assertNotIn("Document Intelligence Summary", body)
        self.assertNotIn("Portfolio Value", body)
        self.assertNotIn("Revenue at Risk", body)
        self.assertIn("Required GL", body)
        self.assertIn("$2M", body)
        self.assertIn("$5M", body)
        self.assertIn("+ New Project", body)
        self.assertIn("+ Add Subcontractor", body)
        self.assertNotIn(">Add Sub</a>", body)
        self.assertNotIn(">+ Project</a>", body)

    def test_processing_document_is_checking_not_needs_attention(self):
        with self.app.app_context():
            project = Project(
                name="Checking Project",
                required_coverage=2_000_000,
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            sub = Subcontractor(
                name="Checking Sub",
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            db.session.add_all([project, sub])
            db.session.flush()
            link = ProjectSubcontractor(
                project_id=project.id,
                subcontractor_id=sub.id,
            )
            doc = Document(
                filename="subcontractors/1/checking.pdf",
                original_name="checking.pdf",
                document_type="COI",
                sub_id=sub.id,
                uploaded_by=self.user_id,
                ai_status="not_analyzed",
            )
            db.session.add_all([link, doc])
            db.session.commit()

        self.login()
        body = self.client.get("/dashboard").get_data(as_text=True)

        self.assertIn("CHECKING", body)
        self.assertNotIn("Checking Sub</td>\n<td>Checking Project</td>", body)

    def test_project_view_shows_final_status_and_contract_extraction(self):
        project_id, _ = self.create_project_subcontractor()
        self.login()

        response = self.client.get(f"/project/{project_id}")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("This project is ready to mobilize.", body)
        self.assertIn("Document Intelligence Summary", body)
        self.assertIn(
            "Document analysis results do not replace final mobilization readiness.",
            body,
        )
        self.assertIn("Current GL", body)
        self.assertIn("$5M", body)
        self.assertIn("Required GL", body)
        self.assertIn("$2M", body)
        self.assertIn("Coverage Gap", body)
        self.assertIn("No gap", body)
        self.assertIn("Contract Extraction", body)
        self.assertIn("Preserved", body)
        self.assertIn("Applied", body)
        self.assertIn("No Owner Requirements uploaded yet.", body)

    def test_unsupported_project_documents_do_not_show_analyze(self):
        project_id, _ = self.create_project_subcontractor()
        self.login()

        response = self.client.get(f"/project/{project_id}")
        body = response.get_data(as_text=True)
        scope_index = body.index("<h3>Scope</h3>")
        owner_index = body.index("<h3>Owner Requirements</h3>")
        scope_block = body[scope_index:owner_index]

        self.assertNotIn(">Analyze<", scope_block)

    def test_subcontractor_document_view_separates_intelligence_and_decision(self):
        _, sub_id = self.create_project_subcontractor()
        self.login()

        response = self.client.get(f"/sub/{sub_id}/documents")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Document Intelligence", body)
        self.assertIn("Compliance Decision", body)
        self.assertIn("Current Coverage", body)
        self.assertIn("Required Coverage", body)
        self.assertIn("Coverage Gap", body)
        self.assertIn("Expiration", body)
        self.assertIn("2029-01-01", body)
        self.assertIn("General Liability", body)
        self.assertIn("Each Occurrence", body)
        self.assertIn("General Aggregate", body)
        self.assertIn("Products-Comp/OP Agg", body)
        self.assertIn("$5M", body)
        self.assertIn("$10M", body)
        self.assertNotIn("Confidence", body)
        self.assertNotIn("Evidence State", body)
        self.assertIn("READY", body)
        self.assertNotIn("Coverage limit is below the project requirement.", body)

    def test_templates_do_not_call_business_services(self):
        for template_path in (
            "app/templates/dashboard.html",
            "app/templates/view_project.html",
            "app/templates/view_sub_documents.html",
        ):
            with open(template_path, encoding="utf-8") as template:
                content = template.read()

            self.assertNotIn("calculate_readiness", content)
            self.assertNotIn("generate_compliance_advice", content)
            self.assertNotIn("analyze_and_save_document", content)
            self.assertNotIn("ai_extracted_data|safe", content)


if __name__ == "__main__":
    unittest.main()
