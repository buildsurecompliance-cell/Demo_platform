import os
import unittest

from datetime import date, datetime, timedelta, timezone


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import (
    DOCUMENT_REQUEST_COMPLETED,
    DOCUMENT_REQUEST_PENDING,
    Document,
    DocumentRequest,
    Project,
    ProjectSubcontractor,
    Subcontractor,
    User,
)
from app.services.organizations import create_default_organization_for_user


class CoreUXPolishTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestingConfig)
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
        self.assertIn("1 active project", body)
        self.assertIn("Ready Projects", body)
        self.assertIn("Checking Projects", body)
        self.assertIn("Needs Attention", body)
        self.assertIn("Blocked Projects", body)
        self.assertEqual(body.count('class="ux-metric"'), 4)
        self.assertNotIn("Plan Capacity", body)
        self.assertNotIn("Document Intelligence Summary", body)
        self.assertNotIn("Portfolio Value", body)
        self.assertNotIn("Revenue at Risk", body)
        self.assertNotIn("Required GL", body)
        self.assertNotIn("Project</th>\n<th scope=\"col\">Required GL", body)
        self.assertNotIn("+ New Project", body)
        self.assertNotIn("+ Add Subcontractor", body)
        self.assertNotIn(">Add Sub</a>", body)
        self.assertNotIn(">+ Project</a>", body)
        self.assertNotIn("Search project", body)
        self.assertNotIn("Search subcontractors", body)
        self.assertNotIn('id="projects"', body)
        self.assertNotIn('id="subcontractors"', body)

    def test_sidebar_links_to_real_management_pages(self):
        self.login()

        body = self.client.get("/dashboard").get_data(as_text=True)

        self.assertIn('href="/projects"', body)
        self.assertIn('href="/subcontractors"', body)
        self.assertNotIn("/dashboard#projects", body)
        self.assertNotIn("/dashboard#subcontractors", body)

    def test_projects_list_exists_and_uses_project_readiness(self):
        self.create_project_subcontractor()

        with self.app.app_context():
            blocked_project = Project(
                name="Blocked List Project",
                required_coverage=2_000_000,
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            blocked_sub = Subcontractor(
                name="Blocked List Sub",
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
        response = self.client.get("/projects")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Projects", body)
        self.assertIn("+ New Project", body)
        self.assertIn("Riverside Office Building", body)
        self.assertIn("Blocked List Project", body)
        self.assertIn("READY", body)
        self.assertIn("BLOCKED", body)
        self.assertIn("Required GL", body)
        self.assertIn("View", body)
        self.assertIn("Edit", body)

    def test_projects_list_search_and_organization_scope(self):
        with self.app.app_context():
            visible = Project(
                name="Visible Project",
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            hidden_user = User(email="hidden@example.com", paid=True)
            hidden_user.set_password("password123")
            db.session.add(hidden_user)
            db.session.flush()
            hidden_org = create_default_organization_for_user(hidden_user)
            hidden = Project(
                name="Hidden Project",
                user_id=hidden_user.id,
                organization_id=hidden_org.id,
            )
            db.session.add_all([visible, hidden])
            db.session.commit()

        self.login()
        body = self.client.get("/projects?search=Visible").get_data(as_text=True)

        self.assertIn("Visible Project", body)
        self.assertNotIn("Hidden Project", body)
        self.assertNotIn("No projects yet.", body)

        body = self.client.get("/projects?search=Missing").get_data(as_text=True)
        self.assertIn("No projects yet.", body)

    def test_subcontractors_list_exists_with_documental_status_and_evidence_coverage(self):
        self.create_project_subcontractor()

        self.login()
        response = self.client.get("/subcontractors")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Subcontractors", body)
        self.assertIn("+ Add Subcontractor", body)
        self.assertIn("Ace Concrete", body)
        self.assertIn("VALID", body)
        self.assertIn("$5M", body)
        self.assertIn("View Documents", body)
        self.assertIn("Edit", body)
        self.assertNotIn("COMPLIANT", body)
        self.assertNotIn("BLOCKED", body)

    def test_subcontractors_list_search_status_filter_and_organization_scope(self):
        with self.app.app_context():
            visible = Subcontractor(
                name="Visible Sub",
                email="visible@example.com",
                coi_expiration=date.today() + timedelta(days=60),
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            missing = Subcontractor(
                name="Missing Sub",
                email="missing@example.com",
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            hidden_user = User(email="hidden-sub@example.com", paid=True)
            hidden_user.set_password("password123")
            db.session.add(hidden_user)
            db.session.flush()
            hidden_org = create_default_organization_for_user(hidden_user)
            hidden = Subcontractor(
                name="Hidden Sub",
                email="hidden@example.com",
                user_id=hidden_user.id,
                organization_id=hidden_org.id,
            )
            db.session.add_all([visible, missing, hidden])
            db.session.commit()

        self.login()
        body = self.client.get(
            "/subcontractors?search=Visible"
        ).get_data(as_text=True)
        self.assertIn("Visible Sub", body)
        self.assertNotIn("Missing Sub", body)
        self.assertNotIn("Hidden Sub", body)

        body = self.client.get(
            "/subcontractors?status=MISSING"
        ).get_data(as_text=True)
        self.assertIn("Missing Sub", body)
        self.assertNotIn("Visible Sub", body)

    def test_subcontractors_list_coverage_does_not_use_legacy_link_limit(self):
        with self.app.app_context():
            project = Project(
                name="Legacy Coverage Project",
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            sub = Subcontractor(
                name="Legacy Coverage Sub",
                email="legacy@example.com",
                coi_expiration=date.today() + timedelta(days=60),
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            db.session.add_all([project, sub])
            db.session.flush()
            db.session.add(
                ProjectSubcontractor(
                    project_id=project.id,
                    subcontractor_id=sub.id,
                    coverage_limit=9_000_000,
                )
            )
            db.session.commit()

        self.login()
        body = self.client.get("/subcontractors").get_data(as_text=True)

        self.assertIn("Legacy Coverage Sub", body)
        self.assertIn("Not available", body)
        self.assertNotIn("$9M", body)

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

        self.assertIn("2 active projects", body)
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
            '<span class="ux-metric-label">Needs Attention</span>',
            body,
        )
        self.assertIn("Blocked Sub", body)
        self.assertIn("Blocked Project", body)
        self.assertIn("COI missing", body)

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
                    email=f"{name.lower().replace(' ', '-')}@example.com",
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
            '<span class="ux-metric-label">Needs Attention</span>',
            body,
        )
        self.assertIn("Missing COI One", body)
        self.assertIn("Missing COI Two", body)
        self.assertEqual(body.count("Request COI"), 2)

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

        self.assertIn(
            '<span class="ux-metric-value">1</span>\n'
            '<span class="ux-metric-label">Checking Projects</span>',
            body,
        )
        self.assertNotIn("Checking Sub</td>\n<td>Checking Project</td>", body)
        self.assertIn("No compliance issues need your attention.", body)

    def test_pending_document_request_is_not_needs_attention(self):
        with self.app.app_context():
            project = Project(
                name="Pending Request Project",
                required_coverage=2_000_000,
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            sub = Subcontractor(
                name="Pending Request Sub",
                email="pending@example.com",
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            db.session.add_all([project, sub])
            db.session.flush()
            link = ProjectSubcontractor(
                project_id=project.id,
                subcontractor_id=sub.id,
            )
            request = DocumentRequest(
                organization_id=self.organization_id,
                project_id=project.id,
                subcontractor_id=sub.id,
                document_type="COI",
                token_hash="pending-request-token",
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
                + timedelta(days=3),
                status=DOCUMENT_REQUEST_PENDING,
                created_by_user_id=self.user_id,
            )
            db.session.add_all([link, request])
            db.session.commit()

        self.login()
        body = self.client.get("/dashboard").get_data(as_text=True)

        self.assertNotIn("Pending Request Sub</td>", body)
        self.assertIn("No compliance issues need your attention.", body)

    def test_expired_issue_includes_date_and_no_email_add_email_action(self):
        with self.app.app_context():
            project = Project(
                name="Expired Project",
                required_coverage=None,
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            sub = Subcontractor(
                name="No Email Expired Sub",
                coi_expiration=date(2026, 8, 1),
                user_id=self.user_id,
                organization_id=self.organization_id,
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

        self.login()
        body = self.client.get("/dashboard").get_data(as_text=True)

        self.assertIn("No Email Expired Sub", body)
        self.assertIn("COI expired Aug 1, 2026", body)
        self.assertIn("Add Email", body)
        self.assertNotIn("Request COI", body)

    def test_pending_attention_item_does_not_count_as_blocked_project(self):
        with self.app.app_context():
            project = Project(
                name="Expiring Soon Project",
                required_coverage=None,
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            sub = Subcontractor(
                name="Expiring Soon Sub",
                email="expiring@example.com",
                coi_expiration=date.today() + timedelta(days=10),
                user_id=self.user_id,
                organization_id=self.organization_id,
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

        self.login()
        body = self.client.get("/dashboard").get_data(as_text=True)

        self.assertIn("Expiring Soon Sub", body)
        self.assertIn("COI expires", body)
        self.assertIn(
            '<span class="ux-metric-value">0</span>\n'
            '<span class="ux-metric-label">Blocked Projects</span>',
            body,
        )

    def test_completed_request_and_coverage_deficit_show_correction_action(self):
        with self.app.app_context():
            project = Project(
                name="Correction Project",
                required_coverage=5_000_000,
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            sub = Subcontractor(
                name="Correction Sub",
                email="correction@example.com",
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            db.session.add_all([project, sub])
            db.session.flush()
            link = ProjectSubcontractor(
                project_id=project.id,
                subcontractor_id=sub.id,
                coverage_limit=10_000_000,
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
                    "coverage_limit": 2_000_000,
                    "general_liability": {
                        "each_occurrence": 2_000_000,
                    },
                    "confidence": 0.95,
                },
                ai_compliance_result={
                    "status": "Ready",
                    "issues": [],
                    "warnings": [],
                },
            )
            request = DocumentRequest(
                organization_id=self.organization_id,
                project_id=project.id,
                subcontractor_id=sub.id,
                document_type="COI",
                token_hash="completed-request-token",
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
                + timedelta(days=3),
                status=DOCUMENT_REQUEST_COMPLETED,
                created_by_user_id=self.user_id,
            )
            db.session.add_all([link, doc, request])
            db.session.commit()

        self.login()
        body = self.client.get("/dashboard").get_data(as_text=True)

        self.assertIn("Correction Sub", body)
        self.assertIn("GL $2M / Required $5M", body)
        self.assertIn("Request Corrected COI", body)
        self.assertNotIn("$10M", body)

    def test_failed_document_analysis_shows_review_documents_action(self):
        with self.app.app_context():
            project = Project(
                name="Review Project",
                required_coverage=None,
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            sub = Subcontractor(
                name="Review Sub",
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
                filename="subcontractors/1/unreadable.pdf",
                original_name="unreadable.pdf",
                document_type="COI",
                sub_id=sub.id,
                uploaded_by=self.user_id,
                ai_status="failed",
            )
            db.session.add_all([link, doc])
            db.session.commit()

        self.login()
        body = self.client.get("/dashboard").get_data(as_text=True)

        self.assertIn("Review Sub", body)
        self.assertIn("COI analysis failed", body)
        self.assertIn("Review Documents", body)
        self.assertNotIn("Add Email", body)

    def test_dashboard_removes_management_tables_and_status_column(self):
        self.create_project_subcontractor()
        self.login()

        body = self.client.get("/dashboard").get_data(as_text=True)

        self.assertNotIn(">Reminder<", body)
        self.assertNotIn("/send_reminder", body)
        self.assertNotIn("Contract Status", body)
        self.assertNotIn("All Contract Status", body)
        self.assertNotIn("Search project", body)
        self.assertNotIn("Search subcontractors", body)
        self.assertNotIn("<th scope=\"col\">Status</th>", body)
        self.assertNotIn("<th scope=\"col\">Trade</th>", body)
        self.assertNotIn("<th scope=\"col\">Coverage</th>", body)

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
