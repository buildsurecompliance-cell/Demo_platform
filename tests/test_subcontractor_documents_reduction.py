import os
import unittest

from datetime import date, timedelta
from unittest.mock import patch


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import (
    Document,
    Project,
    ProjectSubcontractor,
    Subcontractor,
    User,
)
from app.services.organizations import create_default_organization_for_user


class SubcontractorDocumentsReductionTest(unittest.TestCase):

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

            self.user = User(email="owner@example.com", paid=True)
            self.user.set_password("password123")
            self.other_user = User(email="other@example.com", paid=True)
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

    def make_subcontractor(self, **overrides):
        sub = Subcontractor(
            name=overrides.pop("name", "Apex Concrete"),
            role=overrides.pop("role", "Concrete"),
            email=overrides.pop("email", "apex@example.com"),
            user_id=overrides.pop("user_id", self.user_id),
            organization_id=overrides.pop(
                "organization_id",
                self.organization_id,
            ),
            **overrides,
        )
        db.session.add(sub)
        db.session.flush()
        return sub

    def make_project(self, **overrides):
        project = Project(
            name=overrides.pop("name", "Riverside Office Building"),
            required_coverage=overrides.pop("required_coverage", 2_000_000),
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

    def link(self, project, sub, coverage_limit=None):
        link = ProjectSubcontractor(
            project_id=project.id,
            subcontractor_id=sub.id,
            coverage_limit=coverage_limit,
        )
        db.session.add(link)
        db.session.flush()
        return link

    def add_coi(
        self,
        sub,
        *,
        original_name="coi.pdf",
        version=1,
        expiration=None,
        coverage=2_000_000,
        ai_status="analyzed",
        compliance_result=None,
    ):
        document = Document(
            filename=f"subcontractors/{sub.id}/{original_name}",
            original_name=original_name,
            document_type="COI",
            version=version,
            sub_id=sub.id,
            uploaded_by=self.user_id,
            ai_status=ai_status,
            ai_confidence=0.95,
            ai_extracted_data={
                "expiration_date": (
                    expiration.isoformat()
                    if hasattr(expiration, "isoformat")
                    else expiration
                ),
                "general_liability_limit": coverage,
                "general_liability": {
                    "each_occurrence": coverage,
                    "general_aggregate": 5_000_000,
                    "products_completed_operations": 5_000_000,
                    "expiration_date": (
                        expiration.isoformat()
                        if hasattr(expiration, "isoformat")
                        else expiration
                    ),
                },
                "confidence": 0.95,
            },
            ai_compliance_result=(
                compliance_result
                if compliance_result is not None
                else {
                    "status": "Ready",
                    "issues": [],
                    "warnings": [{"message": "raw warning"}],
                    "confidence": 0.95,
                }
            ),
        )
        db.session.add(document)
        db.session.flush()
        return document

    def render_documents(self, sub_id):
        self.login()
        return self.client.get(f"/sub/{sub_id}/documents")

    def test_current_coi_summary_uses_global_statuses_not_readiness(self):
        with self.app.app_context():
            valid = self.make_subcontractor(name="Valid Sub")
            self.add_coi(
                valid,
                expiration=date.today() + timedelta(days=90),
                coverage=2_000_000,
            )
            expired = self.make_subcontractor(name="Expired Sub")
            self.add_coi(
                expired,
                expiration=date.today() - timedelta(days=1),
                coverage=1_000_000,
            )
            checking = self.make_subcontractor(name="Checking Sub")
            self.add_coi(
                checking,
                ai_status="not_analyzed",
                expiration=None,
                coverage=None,
                compliance_result=None,
            )
            missing = self.make_subcontractor(name="Missing Sub")
            db.session.commit()
            ids = {
                "valid": valid.id,
                "expired": expired.id,
                "checking": checking.id,
                "missing": missing.id,
            }

        valid_body = self.render_documents(ids["valid"]).get_data(as_text=True)
        expired_body = self.render_documents(ids["expired"]).get_data(as_text=True)
        checking_body = self.render_documents(ids["checking"]).get_data(as_text=True)
        missing_body = self.render_documents(ids["missing"]).get_data(as_text=True)

        self.assertIn("VALID", valid_body)
        self.assertIn("GL Coverage $2M", valid_body)
        self.assertNotIn("READY", valid_body)
        self.assertNotIn("BLOCKED", valid_body)
        self.assertIn("EXPIRED", expired_body)
        self.assertIn("CHECKING", checking_body)
        self.assertIn("BuildSure is reviewing the latest COI.", checking_body)
        self.assertIn("MISSING", missing_body)
        self.assertIn("No COI on file.", missing_body)

    def test_summary_coverage_uses_validated_evidence_not_legacy_limit(self):
        with self.app.app_context():
            sub = self.make_subcontractor()
            project = self.make_project(required_coverage=2_000_000)
            self.link(project, sub, coverage_limit=9_000_000)
            self.add_coi(
                sub,
                expiration=date.today() + timedelta(days=90),
                coverage=2_000_000,
            )
            db.session.commit()
            sub_id = sub.id

        body = self.render_documents(sub_id).get_data(as_text=True)

        self.assertIn("GL Coverage $2M", body)
        self.assertNotIn("$9M", body)

    def test_project_readiness_uses_calculate_readiness_once_per_project(self):
        with self.app.app_context():
            sub = self.make_subcontractor()
            project = self.make_project()
            self.link(project, sub)
            db.session.commit()
            sub_id = sub.id

        with patch(
            "app.routes.subcontractors.calculate_readiness",
            return_value={"status": "READY", "reasons": []},
        ) as readiness_mock:
            body = self.render_documents(sub_id).get_data(as_text=True)

        self.assertEqual(readiness_mock.call_count, 1)
        self.assertIn("Project Readiness", body)
        self.assertIn("READY", body)
        self.assertIn("Meets project requirement", body)

    def test_project_readiness_uses_shared_primary_issue_label(self):
        with self.app.app_context():
            sub = self.make_subcontractor()
            project = self.make_project()
            self.link(project, sub)
            db.session.commit()
            sub_id = sub.id

        with patch(
            "app.routes.subcontractors.calculate_readiness",
            return_value={
                "status": "BLOCKED",
                "reasons": [
                    {
                        "code": "COI_MISSING",
                        "message": "Certificate of Insurance is missing.",
                    }
                ],
            },
        ), patch(
            "app.routes.subcontractors.primary_issue_label",
            return_value="Shared issue",
        ) as issue_mock:
            body = self.render_documents(sub_id).get_data(as_text=True)

        self.assertEqual(issue_mock.call_count, 1)
        self.assertIn("BLOCKED", body)
        self.assertIn("Shared issue", body)

    def test_expired_coverage_and_missing_issues_match_other_views(self):
        with self.app.app_context():
            expired_sub = self.make_subcontractor(name="Expired Coverage Sub")
            expired_project = self.make_project(
                name="Expired Project",
                required_coverage=5_000_000,
            )
            self.link(expired_project, expired_sub)
            self.add_coi(
                expired_sub,
                expiration=date(2025, 6, 30),
                coverage=2_000_000,
            )

            coverage_sub = self.make_subcontractor(name="Coverage Sub")
            coverage_project = self.make_project(
                name="Coverage Project",
                required_coverage=10_000_000,
            )
            self.link(coverage_project, coverage_sub)
            self.add_coi(
                coverage_sub,
                expiration=date.today() + timedelta(days=90),
                coverage=2_000_000,
            )

            missing_sub = self.make_subcontractor(name="Missing Sub")
            missing_project = self.make_project(name="Missing Project")
            self.link(missing_project, missing_sub)
            db.session.commit()
            ids = {
                "expired_sub": expired_sub.id,
                "expired_project": expired_project.id,
                "coverage_sub": coverage_sub.id,
                "coverage_project": coverage_project.id,
                "missing_sub": missing_sub.id,
                "missing_project": missing_project.id,
            }

        expired_sub_body = self.render_documents(
            ids["expired_sub"]
        ).get_data(as_text=True)
        expired_project_body = self.client.get(
            f"/project/{ids['expired_project']}"
        ).get_data(as_text=True)
        dashboard_body = self.client.get("/dashboard").get_data(as_text=True)
        coverage_sub_body = self.client.get(
            f"/sub/{ids['coverage_sub']}/documents"
        ).get_data(as_text=True)
        coverage_project_body = self.client.get(
            f"/project/{ids['coverage_project']}"
        ).get_data(as_text=True)
        missing_sub_body = self.client.get(
            f"/sub/{ids['missing_sub']}/documents"
        ).get_data(as_text=True)
        missing_project_body = self.client.get(
            f"/project/{ids['missing_project']}"
        ).get_data(as_text=True)

        self.assertIn("COI expired Jun 30, 2025", expired_sub_body)
        self.assertIn("COI expired Jun 30, 2025", expired_project_body)
        self.assertIn("COI expired Jun 30, 2025", dashboard_body)
        self.assertIn("GL $2M / Required $10M", coverage_sub_body)
        self.assertIn("GL $2M / Required $10M", coverage_project_body)
        self.assertIn("GL $2M / Required $10M", dashboard_body)
        self.assertIn("COI missing", missing_sub_body)
        self.assertIn("COI missing", missing_project_body)

    def test_checking_project_has_no_human_action(self):
        with self.app.app_context():
            sub = self.make_subcontractor(name="Checking Project Sub")
            project = self.make_project()
            self.link(project, sub)
            self.add_coi(
                sub,
                ai_status="not_analyzed",
                expiration=None,
                coverage=None,
                compliance_result=None,
            )
            db.session.commit()
            sub_id = sub.id

        body = self.render_documents(sub_id).get_data(as_text=True)

        self.assertIn("CHECKING", body)
        self.assertIn("BuildSure is reviewing the latest COI.", body)
        self.assertIn("View Project", body)
        self.assertNotIn("Request COI", body)
        self.assertNotIn("Request Corrected COI", body)
        self.assertNotIn("Resend", body)
        self.assertNotIn("Add Email", body)

    def test_readiness_is_not_repeated_per_historical_document_version(self):
        with self.app.app_context():
            sub = self.make_subcontractor()
            project = self.make_project()
            self.link(project, sub)
            self.add_coi(
                sub,
                original_name="coi-v1.pdf",
                version=1,
                expiration=date.today() + timedelta(days=90),
            )
            self.add_coi(
                sub,
                original_name="coi-v2.pdf",
                version=2,
                expiration=date.today() + timedelta(days=120),
            )
            db.session.commit()
            sub_id = sub.id

        body = self.render_documents(sub_id).get_data(as_text=True)

        self.assertEqual(body.count("Project Readiness"), 1)
        self.assertEqual(body.count("Riverside Office Building"), 1)
        self.assertIn("coi-v1.pdf", body)
        self.assertIn("coi-v2.pdf", body)

    def test_document_history_is_compact_and_hides_intelligence_details(self):
        with self.app.app_context():
            sub = self.make_subcontractor()
            self.add_coi(
                sub,
                original_name="analyzed.pdf",
                version=1,
                expiration=date.today() + timedelta(days=90),
                ai_status="analyzed",
            )
            self.add_coi(
                sub,
                original_name="processing.pdf",
                version=2,
                ai_status="not_analyzed",
                expiration=None,
                coverage=None,
                compliance_result=None,
            )
            self.add_coi(
                sub,
                original_name="failed.pdf",
                version=3,
                ai_status="failed",
                expiration=None,
                coverage=None,
                compliance_result={"issues": [{"message": "raw issue"}]},
            )
            db.session.commit()
            sub_id = sub.id

        body = self.render_documents(sub_id).get_data(as_text=True)

        self.assertIn("Documents", body)
        self.assertIn("v1", body)
        self.assertIn("v2", body)
        self.assertIn("v3", body)
        self.assertIn("ANALYZED", body)
        self.assertIn("PROCESSING", body)
        self.assertIn("FAILED", body)
        self.assertIn("Open", body)
        self.assertIn('method="POST"', body)
        self.assertIn("Delete", body)
        self.assertNotIn("Document Intelligence", body)
        self.assertNotIn("Compliance Decision", body)
        self.assertNotIn("Each Occurrence", body)
        self.assertNotIn("General Aggregate", body)
        self.assertNotIn("Products-Comp/OP", body)
        self.assertNotIn("Confidence", body)
        self.assertNotIn("raw issue", body)
        self.assertNotIn("raw warning", body)

    def test_empty_documents_state_is_small_and_project_readiness_remains(self):
        with self.app.app_context():
            sub = self.make_subcontractor()
            project = self.make_project()
            self.link(project, sub)
            db.session.commit()
            sub_id = sub.id

        body = self.render_documents(sub_id).get_data(as_text=True)

        self.assertIn("MISSING", body)
        self.assertIn("No COI on file.", body)
        self.assertIn("Project Readiness", body)
        self.assertIn("COI missing", body)
        self.assertIn("No documents uploaded.", body)

    def test_tenant_isolation_is_preserved(self):
        with self.app.app_context():
            other_sub = self.make_subcontractor(
                name="Other Sub",
                user_id=self.other_user_id,
                organization_id=self.other_organization_id,
            )
            db.session.commit()
            other_sub_id = other_sub.id

        self.login(self.user_id, self.organization_id)
        response = self.client.get(f"/sub/{other_sub_id}/documents")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
