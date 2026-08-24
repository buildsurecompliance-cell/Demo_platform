import os
import unittest

from datetime import date, datetime, timedelta
from unittest.mock import patch


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
from app.routes import projects as project_routes
from app.services.document_requests import hash_document_request_token
from app.services.organizations import create_default_organization_for_user


class ProjectComplianceAdviceViewTest(unittest.TestCase):

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

            self.user = User(
                email="owner@example.com",
                paid=True,
            )
            self.user.set_password("password123")

            self.other_user = User(
                email="other@example.com",
                paid=True,
            )
            self.other_user.set_password("password123")

            db.session.add_all([self.user, self.other_user])
            db.session.flush()

            self.organization = create_default_organization_for_user(self.user)
            self.other_organization = create_default_organization_for_user(
                self.other_user
            )
            db.session.commit()

            self.user_id = self.user.id
            self.other_user_id = self.other_user.id
            self.organization_id = self.organization.id
            self.other_organization_id = self.other_organization.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def login(self, user_id=None, organization_id=None):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id or self.user_id)
            session["_fresh"] = True
            session["organization_id"] = organization_id or self.organization_id
            session["active_organization_id"] = (
                organization_id or self.organization_id
            )

    def make_project(self, **overrides):
        project = Project(
            name=overrides.pop("name", "Test Project"),
            required_coverage=overrides.pop("required_coverage", 2_000_000),
            end_date=overrides.pop(
                "end_date",
                date.today() + timedelta(days=90),
            ),
            user_id=overrides.pop("user_id", self.user_id),
            organization_id=overrides.pop(
                "organization_id",
                self.organization_id,
            ),
            **overrides,
        )
        db.session.add(project)
        db.session.flush()
        return project

    def make_subcontractor(self, **overrides):
        subcontractor = Subcontractor(
            name=overrides.pop("name", "Subcontractor"),
            role=overrides.pop("role", "Trade"),
            email=overrides.pop("email", "sub@example.com"),
            user_id=overrides.pop("user_id", self.user_id),
            organization_id=overrides.pop(
                "organization_id",
                self.organization_id,
            ),
            **overrides,
        )
        db.session.add(subcontractor)
        db.session.flush()
        return subcontractor

    def link(self, project, subcontractor):
        project_subcontractor = ProjectSubcontractor(
            project_id=project.id,
            subcontractor_id=subcontractor.id,
        )
        db.session.add(project_subcontractor)
        db.session.flush()
        return project_subcontractor

    def add_coi_document(
        self,
        subcontractor,
        expiration=None,
        coverage=2_000_000,
        ai_status="analyzed",
        confidence=0.95,
        compliance_result=None,
    ):
        extracted_data = {
            "confidence": confidence,
            "expiration_date": (
                expiration.isoformat()
                if hasattr(expiration, "isoformat")
                else expiration
            ),
            "general_liability": {
                "each_occurrence": coverage,
                "expiration_date": (
                    expiration.isoformat()
                    if hasattr(expiration, "isoformat")
                    else expiration
                ),
            },
            "general_liability_limit": coverage,
            "coverage_limit": coverage,
        }
        document = Document(
            filename=f"subcontractors/{subcontractor.id}/coi.pdf",
            original_name="coi.pdf",
            document_type="COI",
            sub_id=subcontractor.id,
            uploaded_by=self.user_id,
            ai_status=ai_status,
            ai_confidence=confidence,
            ai_extracted_data=extracted_data,
            ai_compliance_result=(
                compliance_result
                if compliance_result is not None
                else {
                    "status": "Ready",
                    "issues": [],
                    "warnings": [],
                    "confidence": confidence,
                }
            ),
        )
        db.session.add(document)
        db.session.flush()
        return document

    def add_request(self, project, subcontractor, status, sent_at=None):
        document_request = DocumentRequest(
            organization_id=project.organization_id,
            project_id=project.id,
            subcontractor_id=subcontractor.id,
            document_type="COI",
            token_hash=hash_document_request_token(
                f"{project.id}-{subcontractor.id}-{status}"
            ),
            status=status,
            expires_at=datetime.utcnow() + timedelta(days=7),
            created_by_user_id=self.user_id,
            last_sent_at=sent_at,
            completed_at=(
                sent_at
                if status == DOCUMENT_REQUEST_COMPLETED
                else None
            ),
        )
        db.session.add(document_request)
        db.session.flush()
        return document_request

    def render_project(self, project_id):
        self.login()
        return self.client.get(f"/project/{project_id}")

    def test_project_summary_uses_dashboard_readiness_source(self):
        with self.app.app_context():
            project = self.make_project()
            db.session.commit()
            project_id = project.id

        self.login()
        with patch(
            "app.routes.projects.dashboard_readiness_for_project",
            return_value="CHECKING",
        ) as readiness_mock:
            response = self.client.get(f"/project/{project_id}")

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(readiness_mock.call_count, 1)
        self.assertIn("CHECKING", body)
        self.assertIn("BuildSure is checking subcontractor documents.", body)

    def test_empty_project_is_not_ready(self):
        with self.app.app_context():
            project = self.make_project()
            db.session.commit()
            project_id = project.id

        body = self.render_project(project_id).get_data(as_text=True)

        self.assertIn("NO SUBCONTRACTORS", body)
        self.assertIn("No subcontractors assigned.", body)
        self.assertNotIn("This project is ready to mobilize.", body)

    def test_ready_row_shows_status_expiration_and_documents_action_only(self):
        with self.app.app_context():
            project = self.make_project()
            subcontractor = self.make_subcontractor(name="Ready Sub")
            self.link(project, subcontractor)
            self.add_coi_document(
                subcontractor,
                expiration=date.today() + timedelta(days=90),
                coverage=2_000_000,
            )
            db.session.commit()
            project_id = project.id

        body = self.render_project(project_id).get_data(as_text=True)

        self.assertIn("Ready Sub", body)
        self.assertIn("READY", body)
        self.assertIn("COI expires", body)
        self.assertIn("View Documents", body)
        self.assertNotIn("Coverage Gap", body)
        self.assertNotIn("Recommended Action", body)
        self.assertNotIn("More actions", body)
        self.assertNotIn("Request COI", body)

    def test_coverage_blocker_uses_validated_evidence_only(self):
        with self.app.app_context():
            project = self.make_project(required_coverage=5_000_000)
            subcontractor = self.make_subcontractor(name="Coverage Sub")
            link = self.link(project, subcontractor)
            link.coverage_limit = 9_000_000
            self.add_coi_document(
                subcontractor,
                expiration=date.today() + timedelta(days=90),
                coverage=2_000_000,
            )
            db.session.commit()
            project_id = project.id

        body = self.render_project(project_id).get_data(as_text=True)

        self.assertIn("Coverage Sub", body)
        self.assertIn("GL $2M / Required $5M", body)
        self.assertIn("Request Corrected COI", body)
        self.assertNotIn("$9M", body)

    def test_expired_coi_is_primary_issue_even_with_coverage_gap(self):
        with self.app.app_context():
            project = self.make_project(required_coverage=5_000_000)
            subcontractor = self.make_subcontractor(name="Expired Coverage Sub")
            self.link(project, subcontractor)
            self.add_coi_document(
                subcontractor,
                expiration=date(2025, 6, 30),
                coverage=2_000_000,
            )
            db.session.commit()
            project_id = project.id

        project_body = self.render_project(project_id).get_data(as_text=True)
        dashboard_body = self.client.get("/dashboard").get_data(as_text=True)

        self.assertIn("COI expired Jun 30, 2025", project_body)
        self.assertIn("COI expired Jun 30, 2025", dashboard_body)
        self.assertNotIn("GL $2M / Required $5M", project_body)
        self.assertNotIn("GL $2M / Required $5M", dashboard_body)

    def test_pending_request_shows_waiting_and_resend(self):
        with self.app.app_context():
            project = self.make_project()
            subcontractor = self.make_subcontractor(name="Waiting Sub")
            self.link(project, subcontractor)
            self.add_request(
                project,
                subcontractor,
                DOCUMENT_REQUEST_PENDING,
                sent_at=datetime(2026, 8, 20),
            )
            db.session.commit()
            project_id = project.id

        body = self.render_project(project_id).get_data(as_text=True)

        self.assertIn("Waiting on subcontractor", body)
        self.assertIn("COI request sent Aug 20", body)
        self.assertIn(">Resend<", body)
        self.assertNotIn(">Request COI<", body)

    def test_completed_invalid_coi_requests_corrected_coi(self):
        with self.app.app_context():
            project = self.make_project(required_coverage=5_000_000)
            subcontractor = self.make_subcontractor(name="Completed Sub")
            self.link(project, subcontractor)
            self.add_coi_document(
                subcontractor,
                expiration=date.today() + timedelta(days=90),
                coverage=2_000_000,
            )
            self.add_request(
                project,
                subcontractor,
                DOCUMENT_REQUEST_COMPLETED,
                sent_at=datetime(2026, 8, 20),
            )
            db.session.commit()
            project_id = project.id

        body = self.render_project(project_id).get_data(as_text=True)

        self.assertIn("GL $2M / Required $5M", body)
        self.assertIn("COI received Aug 20", body)
        self.assertIn("Request Corrected COI", body)

    def test_missing_email_shows_add_email(self):
        with self.app.app_context():
            project = self.make_project()
            subcontractor = self.make_subcontractor(
                name="No Email Sub",
                email="",
            )
            self.link(project, subcontractor)
            db.session.commit()
            project_id = project.id

        body = self.render_project(project_id).get_data(as_text=True)

        self.assertIn("COI missing", body)
        self.assertIn("Add Email", body)

    def test_processing_document_is_checking_without_request_action(self):
        with self.app.app_context():
            project = self.make_project(required_coverage=2_000_000)
            subcontractor = self.make_subcontractor(name="Checking Sub")
            self.link(project, subcontractor)
            self.add_coi_document(
                subcontractor,
                expiration=None,
                coverage=None,
                ai_status="not_analyzed",
                compliance_result=None,
            )
            db.session.commit()
            project_id = project.id

        body = self.render_project(project_id).get_data(as_text=True)

        self.assertIn("Checking Sub", body)
        self.assertIn("CHECKING", body)
        self.assertIn("BuildSure is reviewing the latest COI.", body)
        self.assertNotIn("Request COI", body)

    def test_analysis_failure_shows_review_documents(self):
        with self.app.app_context():
            project = self.make_project()
            subcontractor = self.make_subcontractor(name="Failed Sub")
            self.link(project, subcontractor)
            self.add_coi_document(
                subcontractor,
                expiration=None,
                coverage=None,
                ai_status="failed",
                compliance_result=None,
            )
            db.session.commit()
            project_id = project.id

        body = self.render_project(project_id).get_data(as_text=True)

        self.assertIn("COI analysis needs review", body)
        self.assertIn("Review Documents", body)

    def test_project_documents_are_simple_table_without_intelligence_cards(self):
        with self.app.app_context():
            project = self.make_project()
            db.session.add_all(
                [
                    Document(
                        filename="projects/1/contract.pdf",
                        original_name="contract.pdf",
                        document_type="Contract",
                        project_id=project.id,
                        uploaded_by=self.user_id,
                        ai_status="analyzed",
                        ai_extracted_data={
                            "project_name": "<script>Bad</script>",
                            "contract_value": 4_850_000,
                        },
                        ai_compliance_result={"status": "Ready"},
                    ),
                    Document(
                        filename="projects/1/contract-processing.pdf",
                        original_name="contract-processing.pdf",
                        document_type="Contract",
                        project_id=project.id,
                        uploaded_by=self.user_id,
                        ai_status="not_analyzed",
                    ),
                    Document(
                        filename="projects/1/contract-failed.pdf",
                        original_name="contract-failed.pdf",
                        document_type="Contract",
                        project_id=project.id,
                        uploaded_by=self.user_id,
                        ai_status="failed",
                    ),
                    Document(
                        filename="projects/1/scope.pdf",
                        original_name="scope.pdf",
                        document_type="Scope",
                        project_id=project.id,
                        uploaded_by=self.user_id,
                        ai_status="not_analyzed",
                    ),
                    Document(
                        filename="projects/1/owner.pdf",
                        original_name="owner.pdf",
                        document_type="Owner Requirements",
                        project_id=project.id,
                        uploaded_by=self.user_id,
                        ai_status="not_analyzed",
                    ),
                ]
            )
            db.session.commit()
            project_id = project.id

        body = self.render_project(project_id).get_data(as_text=True)

        self.assertIn("Project Documents", body)
        self.assertIn("contract.pdf", body)
        self.assertIn("ANALYZED", body)
        self.assertIn("contract-processing.pdf", body)
        self.assertIn("PROCESSING", body)
        self.assertIn("contract-failed.pdf", body)
        self.assertIn("FAILED", body)
        self.assertIn("scope.pdf", body)
        self.assertIn("owner.pdf", body)
        self.assertIn("UPLOADED", body)
        self.assertNotIn("Document Intelligence Summary", body)
        self.assertNotIn("Contract Extraction", body)
        self.assertNotIn("Risk Level", body)
        self.assertNotIn("<script>Bad</script>", body)

    def test_project_without_documents_shows_single_empty_message(self):
        with self.app.app_context():
            project = self.make_project()
            db.session.commit()
            project_id = project.id

        body = self.render_project(project_id).get_data(as_text=True)

        self.assertIn("No project documents uploaded.", body)
        self.assertNotIn("No Owner Requirements uploaded yet.", body)

    def test_subcontractors_are_sorted_by_operational_priority(self):
        with self.app.app_context():
            project = self.make_project(required_coverage=5_000_000)
            blocked = self.make_subcontractor(name="Blocked Sub")
            handled = self.make_subcontractor(name="Handled Sub")
            checking = self.make_subcontractor(name="Checking Sub")
            ready = self.make_subcontractor(name="Ready Sub")
            self.link(project, ready)
            self.add_coi_document(
                ready,
                expiration=date.today() + timedelta(days=90),
                coverage=5_000_000,
            )
            self.link(project, checking)
            self.add_coi_document(
                checking,
                ai_status="not_analyzed",
                coverage=None,
            )
            self.link(project, handled)
            self.add_request(
                project,
                handled,
                DOCUMENT_REQUEST_PENDING,
                sent_at=datetime(2026, 8, 20),
            )
            self.link(project, blocked)
            db.session.commit()
            project_id = project.id

        body = self.render_project(project_id).get_data(as_text=True)

        self.assertLess(body.index("Blocked Sub"), body.index("Handled Sub"))
        self.assertLess(body.index("Handled Sub"), body.index("Checking Sub"))
        self.assertLess(body.index("Checking Sub"), body.index("Ready Sub"))

    def test_project_view_preserves_tenant_isolation(self):
        with self.app.app_context():
            project = self.make_project(
                user_id=self.other_user_id,
                organization_id=self.other_organization_id,
            )
            db.session.commit()
            project_id = project.id

        self.login(self.user_id, self.organization_id)
        response = self.client.get(f"/project/{project_id}")

        self.assertEqual(response.status_code, 404)

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

    def test_view_model_contract_is_predictable(self):
        with self.app.app_context():
            project = self.make_project()
            subcontractor = self.make_subcontractor()
            link = self.link(project, subcontractor)
            db.session.commit()

            row = project_routes._project_subcontractor_view_model(link)

        self.assertEqual(
            set(row.keys()),
            {
                "project_subcontractor",
                "status",
                "issue",
                "document_request_status",
                "action",
                "coi_expiration",
                "sort",
            },
        )


if __name__ == "__main__":
    unittest.main()
