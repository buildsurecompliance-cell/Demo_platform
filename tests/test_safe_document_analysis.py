import os
import re
import unittest

from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import patch


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Document, Project, ProjectSubcontractor, Subcontractor, User
from app.services.document_analysis_service import analyze_and_save_document
from app.services.document_intelligence.engine import analyze_document_intelligence
from app.services.organizations import create_default_organization_for_user
from app.services.readiness_service import READY, calculate_readiness


@contextmanager
def temporary_analysis_path(_document):
    yield "coi.pdf"


class SafeDocumentAnalysisTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=True,
            PROPAGATE_EXCEPTIONS=False,
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

    def csrf_token(self, path="/login"):
        response = self.client.get(path)
        match = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            response.data,
        )
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True

    def make_sub_document(self, user_id=None, ai_status="not_analyzed"):
        user_id = user_id or self.user_id
        sub = Subcontractor(
            name="Analysis Sub",
            user_id=user_id,
            organization_id=self.organization_id_for_user(user_id),
        )
        db.session.add(sub)
        db.session.flush()
        document = Document(
            filename="analysis.pdf",
            original_name="analysis.pdf",
            document_type="COI",
            sub_id=sub.id,
            uploaded_by=user_id,
            ai_status=ai_status,
        )
        db.session.add(document)
        db.session.commit()
        return document.id

    def make_project_document(self, user_id=None, ai_status="not_analyzed"):
        user_id = user_id or self.user_id
        project = Project(
            name="Analysis Project",
            user_id=user_id,
            organization_id=self.organization_id_for_user(user_id),
        )
        db.session.add(project)
        db.session.flush()
        document = Document(
            filename="project-analysis.pdf",
            original_name="project-analysis.pdf",
            document_type="Contract",
            project_id=project.id,
            ai_status=ai_status,
        )
        db.session.add(document)
        db.session.commit()
        return document.id

    def organization_id_for_user(self, user_id):
        if user_id == self.other_user_id:
            return self.other_organization_id

        return self.organization_id

    def success_result(self, document_id):
        document = db.session.get(Document, document_id)
        document.ai_status = "analyzed"
        db.session.commit()
        return {
            "success": True,
            "error": None,
            "document": document,
            "result": {
                "success": True,
                "coi_data": {},
                "compliance": {},
            },
        }

    def valid_coi_analysis_result(
        self,
        *,
        expiration_date=None,
        expiration_key="expiration_date",
        confidence=0.92,
        issues=None,
        status="Ready",
    ):
        expiration_date = expiration_date or (
            date.today() + timedelta(days=60)
        ).isoformat()
        extracted_data = {
            expiration_key: expiration_date,
            "general_liability_limit": 1000000,
            "insurance_carrier": "Sample Carrier",
            "policy_number": "POL-123",
            "confidence": confidence,
        }

        return {
            "success": True,
            "error": None,
            "category": "coi",
            "extracted_data": extracted_data,
            "compliance": {
                "status": status,
                "issues": issues or [],
                "warnings": [],
                "confidence": confidence,
            },
        }

    def make_subcontractor_and_document(
        self,
        *,
        coi_expiration=None,
        document_type="COI",
        sub_name="Analysis Sub",
    ):
        subcontractor = Subcontractor(
            name=sub_name,
            user_id=self.user_id,
            organization_id=self.organization_id,
            coi_expiration=coi_expiration,
        )
        db.session.add(subcontractor)
        db.session.flush()
        document = Document(
            filename="analysis.pdf",
            original_name="analysis.pdf",
            document_type=document_type,
            sub_id=subcontractor.id,
            uploaded_by=self.user_id,
        )
        db.session.add(document)
        db.session.commit()
        return subcontractor.id, document.id

    def analyze_with_mock_result(self, document_id, result):
        with patch(
            "app.services.document_analysis_service.temporary_document_path",
            new=temporary_analysis_path,
        ), patch(
            "app.services.document_analysis_service.analyze_document_intelligence",
            return_value=result,
        ):
            return analyze_and_save_document(document_id)

    def test_get_analysis_route_does_not_execute_analysis(self):
        with self.app.app_context():
            document_id = self.make_sub_document()

        self.login(self.user_id)

        with patch("app.routes.ai.analyze_and_save_document") as analyze_mock:
            response = self.client.get(f"/documents/{document_id}/analyze")

        self.assertEqual(response.status_code, 405)
        analyze_mock.assert_not_called()

    def test_mock_mode_coi_returns_expiration_and_general_liability(self):
        with patch.dict(os.environ, {"AI_MOCK_MODE": "true"}):
            result = analyze_document_intelligence(
                file_path="unused-in-mock-mode.pdf",
                document_type="COI",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["category"], "coi")
        self.assertEqual(result["compliance"]["status"], "Ready")
        self.assertEqual(result["compliance"]["issues"], [])
        self.assertEqual(result["extracted_data"]["expiration_date"], "2029-01-01")
        self.assertEqual(
            result["extracted_data"]["general_liability_limit"],
            "1000000",
        )

    def test_post_without_csrf_fails(self):
        with self.app.app_context():
            document_id = self.make_sub_document()

        self.login(self.user_id)

        with patch("app.routes.ai.analyze_and_save_document") as analyze_mock:
            response = self.client.post(f"/documents/{document_id}/analyze")

        self.assertEqual(response.status_code, 403)
        analyze_mock.assert_not_called()

    def test_post_with_csrf_executes_analysis_for_subcontractor_document(self):
        with self.app.app_context():
            document_id = self.make_sub_document()

        self.login(self.user_id)
        token = self.csrf_token()

        with patch(
            "app.routes.ai.analyze_and_save_document",
            side_effect=self.success_result,
        ) as analyze_mock:
            response = self.client.post(
                f"/documents/{document_id}/analyze",
                data={"csrf_token": token},
            )

        self.assertEqual(response.status_code, 302)
        analyze_mock.assert_called_once_with(document_id)

        with self.app.app_context():
            document = db.session.get(Document, document_id)
            self.assertEqual(document.ai_status, "analyzed")

    def test_post_with_csrf_executes_analysis_for_project_document(self):
        with self.app.app_context():
            document_id = self.make_project_document()

        self.login(self.user_id)
        token = self.csrf_token()

        with patch(
            "app.routes.ai.analyze_and_save_document",
            side_effect=self.success_result,
        ) as analyze_mock:
            response = self.client.post(
                f"/documents/{document_id}/analyze",
                data={"csrf_token": token},
            )

        self.assertEqual(response.status_code, 302)
        analyze_mock.assert_called_once_with(document_id)

    def test_unauthenticated_user_is_redirected(self):
        with self.app.app_context():
            document_id = self.make_sub_document()

        token = self.csrf_token()
        response = self.client.post(
            f"/documents/{document_id}/analyze",
            data={"csrf_token": token},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)

    def test_user_cannot_analyze_other_users_document(self):
        with self.app.app_context():
            document_id = self.make_sub_document(user_id=self.other_user_id)

        self.login(self.user_id)
        token = self.csrf_token()

        with patch("app.routes.ai.analyze_and_save_document") as analyze_mock:
            response = self.client.post(
                f"/documents/{document_id}/analyze",
                data={"csrf_token": token},
            )

        self.assertEqual(response.status_code, 404)
        analyze_mock.assert_not_called()

    def test_document_not_found_returns_404(self):
        self.login(self.user_id)
        token = self.csrf_token()

        with patch("app.routes.ai.analyze_and_save_document") as analyze_mock:
            response = self.client.post(
                "/documents/9999/analyze",
                data={"csrf_token": token},
            )

        self.assertEqual(response.status_code, 404)
        analyze_mock.assert_not_called()

    def test_analysis_failure_does_not_return_500_or_sensitive_details(self):
        with self.app.app_context():
            document_id = self.make_sub_document()

        self.login(self.user_id)
        token = self.csrf_token()

        with patch(
            "app.routes.ai.analyze_and_save_document",
            return_value={
                "success": False,
                "error": "internal provider failure private payload",
                "document": None,
                "result": None,
            },
        ):
            response = self.client.post(
                f"/documents/{document_id}/analyze",
                data={"csrf_token": token},
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Document analysis failed. Please try again.", response.data)
        self.assertNotIn(b"internal provider failure", response.data)
        self.assertNotIn(b"private payload", response.data)

        with self.app.app_context():
            document = db.session.get(Document, document_id)
            self.assertEqual(document.ai_status, "failed")

    def test_analysis_exception_does_not_return_500_or_sensitive_details(self):
        with self.app.app_context():
            document_id = self.make_sub_document()

        self.login(self.user_id)
        token = self.csrf_token()

        with patch(
            "app.routes.ai.analyze_and_save_document",
            side_effect=RuntimeError("internal storage failure private payload"),
        ):
            response = self.client.post(
                f"/documents/{document_id}/analyze",
                data={"csrf_token": token},
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Document analysis failed. Please try again.", response.data)
        self.assertNotIn(b"internal storage failure", response.data)
        self.assertNotIn(b"private payload", response.data)

        with self.app.app_context():
            document = db.session.get(Document, document_id)
            self.assertEqual(document.ai_status, "failed")

    def test_analyzing_document_does_not_start_duplicate_analysis(self):
        with self.app.app_context():
            document_id = self.make_sub_document(ai_status="analyzing")

        self.login(self.user_id)
        token = self.csrf_token()

        with patch("app.routes.ai.analyze_and_save_document") as analyze_mock:
            response = self.client.post(
                f"/documents/{document_id}/analyze",
                data={"csrf_token": token},
            )

        self.assertEqual(response.status_code, 302)
        analyze_mock.assert_not_called()

    def test_valid_coi_analysis_fills_missing_subcontractor_expiration(self):
        with self.app.app_context():
            expected_expiration = date.today() + timedelta(days=60)
            subcontractor_id, document_id = self.make_subcontractor_and_document()

            result = self.analyze_with_mock_result(
                document_id,
                self.valid_coi_analysis_result(
                    expiration_date=expected_expiration.isoformat(),
                ),
            )

            subcontractor = db.session.get(Subcontractor, subcontractor_id)
            document = db.session.get(Document, document_id)

            self.assertTrue(result["success"])
            self.assertEqual(subcontractor.coi_expiration, expected_expiration)
            self.assertEqual(document.ai_status, "analyzed")

    def test_valid_coi_analysis_does_not_overwrite_manual_expiration(self):
        with self.app.app_context():
            manual_expiration = date.today() + timedelta(days=120)
            extracted_expiration = date.today() + timedelta(days=1)
            subcontractor_id, document_id = self.make_subcontractor_and_document(
                coi_expiration=manual_expiration,
            )

            self.analyze_with_mock_result(
                document_id,
                self.valid_coi_analysis_result(
                    expiration_date=extracted_expiration.isoformat(),
                ),
            )

            subcontractor = db.session.get(Subcontractor, subcontractor_id)

            self.assertEqual(subcontractor.coi_expiration, manual_expiration)

    def test_invalid_analysis_leaves_subcontractor_expiration_null(self):
        with self.app.app_context():
            subcontractor_id, document_id = self.make_subcontractor_and_document()

            self.analyze_with_mock_result(
                document_id,
                {
                    "success": False,
                    "error": "Unreadable document.",
                    "category": "coi",
                    "extracted_data": None,
                    "compliance": None,
                },
            )

            subcontractor = db.session.get(Subcontractor, subcontractor_id)

            self.assertIsNone(subcontractor.coi_expiration)

    def test_low_confidence_analysis_leaves_subcontractor_expiration_null(self):
        with self.app.app_context():
            subcontractor_id, document_id = self.make_subcontractor_and_document()

            self.analyze_with_mock_result(
                document_id,
                self.valid_coi_analysis_result(confidence=0.5),
            )

            subcontractor = db.session.get(Subcontractor, subcontractor_id)

            self.assertIsNone(subcontractor.coi_expiration)

    def test_validator_failed_analysis_leaves_subcontractor_expiration_null(self):
        with self.app.app_context():
            subcontractor_id, document_id = self.make_subcontractor_and_document()

            self.analyze_with_mock_result(
                document_id,
                self.valid_coi_analysis_result(
                    issues=[
                        {
                            "field": "expiration_date",
                            "message": "Expiration date missing.",
                        }
                    ],
                    status="Blocked",
                ),
            )

            subcontractor = db.session.get(Subcontractor, subcontractor_id)

            self.assertIsNone(subcontractor.coi_expiration)

    def test_project_document_analysis_does_not_update_subcontractor(self):
        with self.app.app_context():
            subcontractor = Subcontractor(
                name="Unrelated Sub",
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            project = Project(
                name="Project Document",
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            db.session.add_all([subcontractor, project])
            db.session.flush()
            document = Document(
                filename="project-coi.pdf",
                original_name="project-coi.pdf",
                document_type="COI",
                project_id=project.id,
                uploaded_by=self.user_id,
            )
            db.session.add(document)
            db.session.commit()
            subcontractor_id = subcontractor.id
            document_id = document.id

            self.analyze_with_mock_result(
                document_id,
                self.valid_coi_analysis_result(),
            )

            subcontractor = db.session.get(Subcontractor, subcontractor_id)

            self.assertIsNone(subcontractor.coi_expiration)

    def test_document_analysis_only_updates_own_subcontractor(self):
        with self.app.app_context():
            first_sub_id, document_id = self.make_subcontractor_and_document(
                sub_name="Document Owner",
            )
            second_sub = Subcontractor(
                name="Other Sub",
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            db.session.add(second_sub)
            db.session.commit()
            second_sub_id = second_sub.id

            self.analyze_with_mock_result(
                document_id,
                self.valid_coi_analysis_result(),
            )

            first_sub = db.session.get(Subcontractor, first_sub_id)
            second_sub = db.session.get(Subcontractor, second_sub_id)

            self.assertIsNotNone(first_sub.coi_expiration)
            self.assertIsNone(second_sub.coi_expiration)

    def test_coi_expiration_parses_mmddyyyy_and_iso_keys(self):
        with self.app.app_context():
            first_expected = date.today() + timedelta(days=45)
            second_expected = date.today() + timedelta(days=75)
            first_sub_id, first_document_id = self.make_subcontractor_and_document(
                sub_name="MMDDYYYY Sub",
            )
            second_sub_id, second_document_id = self.make_subcontractor_and_document(
                sub_name="ISO Key Sub",
            )

            self.analyze_with_mock_result(
                first_document_id,
                self.valid_coi_analysis_result(
                    expiration_date=first_expected.strftime("%m/%d/%Y"),
                    expiration_key="policy_expiration_date",
                ),
            )
            self.analyze_with_mock_result(
                second_document_id,
                self.valid_coi_analysis_result(
                    expiration_date=second_expected.isoformat(),
                    expiration_key="coi_expiration",
                ),
            )

            first_sub = db.session.get(Subcontractor, first_sub_id)
            second_sub = db.session.get(Subcontractor, second_sub_id)

            self.assertEqual(first_sub.coi_expiration, first_expected)
            self.assertEqual(second_sub.coi_expiration, second_expected)

    def test_commit_failure_rolls_back_auto_filled_expiration(self):
        with self.app.app_context():
            subcontractor_id, document_id = self.make_subcontractor_and_document()

            with patch(
                "app.services.document_analysis_service.temporary_document_path",
                new=temporary_analysis_path,
            ), patch(
                "app.services.document_analysis_service.analyze_document_intelligence",
                return_value=self.valid_coi_analysis_result(),
            ), patch(
                "app.services.document_analysis_service.db.session.commit",
                side_effect=RuntimeError("commit failed"),
            ):
                with self.assertRaises(RuntimeError):
                    analyze_and_save_document(document_id)

            db.session.rollback()
            subcontractor = db.session.get(Subcontractor, subcontractor_id)

            self.assertIsNone(subcontractor.coi_expiration)

    def test_readiness_uses_auto_filled_expiration_after_analysis(self):
        with self.app.app_context():
            project = Project(
                name="Readiness Project",
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            db.session.add(project)
            db.session.flush()
            subcontractor_id, document_id = self.make_subcontractor_and_document()
            subcontractor = db.session.get(Subcontractor, subcontractor_id)
            link = ProjectSubcontractor(
                project_id=project.id,
                subcontractor_id=subcontractor.id,
            )
            db.session.add(link)
            db.session.commit()
            link_id = link.id

            self.analyze_with_mock_result(
                document_id,
                self.valid_coi_analysis_result(),
            )

            link = db.session.get(ProjectSubcontractor, link_id)
            readiness = calculate_readiness(link)

            self.assertEqual(readiness["status"], READY)


if __name__ == "__main__":
    unittest.main()
