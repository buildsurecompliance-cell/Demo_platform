import os
import re
import unittest

from unittest.mock import patch


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Document, Project, Subcontractor, User


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
            db.session.commit()
            self.user_id = self.user.id
            self.other_user_id = self.other_user.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

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

    def test_get_analysis_route_does_not_execute_analysis(self):
        with self.app.app_context():
            document_id = self.make_sub_document()

        self.login(self.user_id)

        with patch("app.routes.ai.analyze_and_save_document") as analyze_mock:
            response = self.client.get(f"/documents/{document_id}/analyze")

        self.assertEqual(response.status_code, 405)
        analyze_mock.assert_not_called()

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

        self.assertEqual(response.status_code, 200)
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

        self.assertEqual(response.status_code, 200)
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


if __name__ == "__main__":
    unittest.main()
