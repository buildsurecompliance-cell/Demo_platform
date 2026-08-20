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
        self.assertIn("Ready Projects", body)
        self.assertIn("Checking Projects", body)
        self.assertIn("Needs Attention", body)
        self.assertIn("Needs Attention Items", body)
        self.assertIn("Blocked Projects", body)
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

    def test_dashboard_project_counts_and_attention_items_are_separate(self):
        self.create_project_subcontractor()

        with self.app.app_context():
            blocked_project = Project(
                name="Blocked Project",
                required_coverage=2_000_000,
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            blocked_sub = Subcontractor(
                name="Blocked Sub",
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            db.session.add_all([blocked_project, blocked_sub])
            db.session.flush()
            db.session.add(
                ProjectSubcontractor(
                    project_id=blocked_project.id,
                    subcontractor_id=blocked_sub.id,
                )
            )
            db.session.commit()

        self.login()
        body = self.client.get("/dashboard").get_data(as_text=True)

        self.assertIn(
            '<span class="ux-metric-value">2</span>\n'
            '<span class="ux-metric-label">Active Projects</span>',
            body,
        )
        self.assertIn(
            '<span class="ux-metric-value">1</span>\n'
            '<span class="ux-metric-label">Ready Projects</span>',
            body,
        )
        self.assertIn(
            '<span class="ux-metric-value">1</span>\n'
            '<span class="ux-metric-label">Blocked Projects</span>',
            body,
        )
        self.assertIn(
            '<span class="ux-metric-value">1</span>\n'
            '<span class="ux-metric-label">Needs Attention Items</span>',
            body,
        )
        self.assertIn("Blocked Sub", body)
        self.assertIn("Blocked Project", body)

    def test_needs_attention_count_uses_same_source_as_table(self):
        with self.app.app_context():
            project = Project(
                name="Two Exceptions Project",
                required_coverage=2_000_000,
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            db.session.add(project)
            db.session.flush()

            for name in ("Missing COI One", "Missing COI Two"):
                sub = Subcontractor(
                    name=name,
                    user_id=self.user_id,
                    organization_id=self.organization_id,
                )
                db.session.add(sub)
                db.session.flush()
                db.session.add(
                    ProjectSubcontractor(
                        project_id=project.id,
                        subcontractor_id=sub.id,
                    )
                )

            db.session.commit()

        self.login()
        body = self.client.get("/dashboard").get_data(as_text=True)

        self.assertIn(
            '<span class="ux-metric-value">2</span>\n'
            '<span class="ux-metric-label">Needs Attention Items</span>',
            body,
        )
        self.assertIn("Missing COI One", body)
        self.assertIn("Missing COI Two", body)

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

    def test_global_subcontractor_status_is_documental_not_readiness(self):
        with self.app.app_context():
            sub = Subcontractor(
                name="Global Valid Sub",
                coi_expiration=date.today() + timedelta(days=60),
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            db.session.add(sub)
            db.session.commit()

        self.login()
        body = self.client.get("/dashboard").get_data(as_text=True)

        self.assertIn("Global Valid Sub", body)
        self.assertIn("VALID", body)
        self.assertNotIn("COMPLIANT", body)
        self.assertNotIn("READY", body)
        self.assertNotIn("BLOCKED", body)

    def test_global_subcontractor_coverage_requires_valid_evidence(self):
        with self.app.app_context():
            sub = Subcontractor(
                name="Legacy Coverage Sub",
                coi_expiration=date.today() + timedelta(days=60),
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            doc = Document(
                filename="subcontractors/1/coi.pdf",
                original_name="coi.pdf",
                document_type="COI",
                sub_id=None,
                uploaded_by=self.user_id,
                ai_status="analyzed",
                ai_confidence=0.95,
                ai_extracted_data={
                    "expiration_date": "2029-01-01",
                    "coverage_limit": 5_000_000,
                    "general_liability": {
                        "each_occurrence": 5_000_000,
                    },
                    "confidence": 0.95,
                },
            )
            db.session.add(sub)
            db.session.flush()
            doc.sub_id = sub.id
            db.session.add(doc)
            db.session.commit()

        self.login()
        body = self.client.get("/dashboard").get_data(as_text=True)

        self.assertIn("Legacy Coverage Sub", body)
        self.assertIn("Not available", body)
        self.assertNotIn("$5M", body)

    def test_global_manual_reminder_action_is_not_shown(self):
        self.create_project_subcontractor()
        self.login()

        body = self.client.get("/dashboard").get_data(as_text=True)

        self.assertNotIn(">Reminder<", body)
        self.assertNotIn("/send_reminder", body)

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
        self.assertIn("COI Expiration", body)
        self.assertNotIn("Coverage Gap", body)
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
