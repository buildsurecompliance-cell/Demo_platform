import os
import re
import tempfile
import unittest

from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import patch


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import (
    DOCUMENT_REQUEST_CANCELLED,
    DOCUMENT_REQUEST_COMPLETED,
    DOCUMENT_REQUEST_EXPIRED,
    DOCUMENT_REQUEST_PENDING,
    Document,
    DocumentRequest,
    Project,
    ProjectSubcontractor,
    Subcontractor,
    User,
)
from app.services.document_requests import (
    create_or_resend_coi_request,
    document_request_url,
    hash_document_request_token,
)
from app.services.organizations import create_default_organization_for_user
from app.services.readiness_service import BLOCKED, READY, calculate_readiness


class DocumentRequestTest(unittest.TestCase):

    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory()
        self.app = create_app(TestingConfig)
        self.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=True,
            PROPAGATE_EXCEPTIONS=False,
            STORAGE_BACKEND="local",
            UPLOAD_FOLDER=self.uploads.name,
            APPLICATION_BASE_URL="https://buildsure.test",
            RATELIMIT_ENABLED=False,
        )
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()

        db.drop_all()
        db.create_all()

        self.owner = User(email="owner@example.com", paid=True)
        self.owner.set_password("password123")
        self.other_user = User(email="other@example.com", paid=True)
        self.other_user.set_password("password123")
        db.session.add_all([self.owner, self.other_user])
        db.session.flush()

        self.organization = create_default_organization_for_user(self.owner)
        self.other_organization = create_default_organization_for_user(
            self.other_user
        )
        db.session.flush()

        self.project = Project(
            name="Request Project",
            user_id=self.owner.id,
            organization_id=self.organization.id,
            required_coverage=1_000_000,
        )
        self.subcontractor = Subcontractor(
            name="Request Sub",
            email="sub@example.com",
            user_id=self.owner.id,
            organization_id=self.organization.id,
        )
        self.other_subcontractor = Subcontractor(
            name="Other Sub",
            email="other-sub@example.com",
            user_id=self.other_user.id,
            organization_id=self.other_organization.id,
        )
        db.session.add_all(
            [self.project, self.subcontractor, self.other_subcontractor]
        )
        db.session.flush()

        self.project_subcontractor = ProjectSubcontractor(
            project_id=self.project.id,
            subcontractor_id=self.subcontractor.id,
        )
        db.session.add(self.project_subcontractor)
        db.session.commit()

        self.owner_id = self.owner.id
        self.organization_id = self.organization.id
        self.project_id = self.project.id
        self.subcontractor_id = self.subcontractor.id
        self.other_subcontractor_id = self.other_subcontractor.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.app_context.pop()
        self.uploads.cleanup()

    def csrf_token(self, path="/login"):
        response = self.client.get(path)
        match = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            response.data,
        )
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def login_owner(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.owner_id)
            session["_fresh"] = True
            session["active_organization_id"] = self.organization_id

    def create_request(self):
        with patch(
            "app.services.document_requests.send_email_reminder",
            return_value=True,
        ):
            return create_or_resend_coi_request(
                organization=self.organization,
                project=self.project,
                subcontractor=self.subcontractor,
                created_by_user_id=self.owner_id,
            )

    def post_upload(self, token, filename="coi.pdf", content=b"%PDF-1.4 coi"):
        csrf_token = self.csrf_token(f"/document-request/{token}")
        return self.client.post(
            f"/document-request/{token}",
            data={
                "csrf_token": csrf_token,
                "file": (BytesIO(content), filename),
            },
            content_type="multipart/form-data",
        )

    def test_gc_can_create_request_and_send_email(self):
        self.login_owner()
        csrf_token = self.csrf_token(f"/project/{self.project_id}")

        with patch(
            "app.services.document_requests.send_email_reminder",
            return_value=True,
        ) as send_email:
            response = self.client.post(
                (
                    f"/project/{self.project_id}/subcontractor/"
                    f"{self.subcontractor_id}/request-coi"
                ),
                data={"csrf_token": csrf_token},
            )

        self.assertEqual(response.status_code, 302)
        request = DocumentRequest.query.one()
        self.assertEqual(request.status, DOCUMENT_REQUEST_PENDING)
        self.assertEqual(request.document_type, "COI")
        self.assertEqual(request.organization_id, self.organization_id)
        self.assertIsNotNone(request.sent_at)
        self.assertIsNotNone(request.last_sent_at)
        send_email.assert_called_once()

    def test_corrected_coi_request_email_includes_coverage_context(self):
        self.project.required_coverage = 5_000_000
        document = Document(
            filename="subcontractors/1/coi.pdf",
            original_name="coi.pdf",
            document_type="COI",
            sub_id=self.subcontractor_id,
            uploaded_by=self.owner_id,
            ai_status="analyzed",
            ai_confidence=0.95,
            ai_extracted_data={
                "expiration_date": "2027-05-01",
                "coverage_limit": 2_000_000,
                "general_liability": {
                    "each_occurrence": 2_000_000,
                    "expiration_date": "2027-05-01",
                },
                "confidence": 0.95,
            },
            ai_compliance_result={
                "is_coi": True,
                "validator": {"valid": True, "errors": []},
                "confidence": 0.95,
            },
        )
        db.session.add(document)
        db.session.commit()

        with patch(
            "app.services.document_requests.send_email_reminder",
            return_value=True,
        ) as send_email:
            delivery = create_or_resend_coi_request(
                organization=self.organization,
                project=self.project,
                subcontractor=self.subcontractor,
                created_by_user_id=self.owner_id,
            )

        self.assertTrue(delivery.sent)
        _, subject, message = send_email.call_args.args
        self.assertEqual(subject, "Corrected COI requested for Request Project")
        self.assertIn("corrected Certificate of Insurance", message)
        self.assertIn("General Liability: $2M", message)
        self.assertIn("General Liability: $5M", message)

    def test_request_requires_same_organization_and_link(self):
        self.login_owner()
        csrf_token = self.csrf_token(f"/project/{self.project_id}")

        response = self.client.post(
            (
                f"/project/{self.project_id}/subcontractor/"
                f"{self.other_subcontractor_id}/request-coi"
            ),
            data={"csrf_token": csrf_token},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(DocumentRequest.query.count(), 0)

    def test_request_requires_subcontractor_email(self):
        self.subcontractor.email = ""
        db.session.commit()

        with self.assertRaises(ValueError):
            self.create_request()

        self.assertEqual(DocumentRequest.query.count(), 0)

    def test_email_failure_keeps_pending_request_without_sent_timestamps(self):
        with patch(
            "app.services.document_requests.send_email_reminder",
            side_effect=RuntimeError("provider unavailable"),
        ):
            delivery = create_or_resend_coi_request(
                organization=self.organization,
                project=self.project,
                subcontractor=self.subcontractor,
                created_by_user_id=self.owner_id,
            )

        request = DocumentRequest.query.one()
        self.assertFalse(delivery.sent)
        self.assertEqual(request.status, DOCUMENT_REQUEST_PENDING)
        self.assertIsNone(request.sent_at)
        self.assertIsNone(request.last_sent_at)

    def test_raw_token_is_never_stored_and_url_uses_configured_base(self):
        delivery = self.create_request()
        request = delivery.request

        self.assertEqual(request.token_hash, hash_document_request_token(delivery.token))
        self.assertNotEqual(request.token_hash, delivery.token)
        self.assertTrue(document_request_url(delivery.token).startswith(
            "https://buildsure.test/document-request/"
        ))

    def test_valid_link_opens_without_login(self):
        delivery = self.create_request()

        response = self.client.get(f"/document-request/{delivery.token}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Request Project", response.data)
        self.assertIn(b"Request Sub", response.data)

    def test_invalid_expired_and_cancelled_links_are_generic(self):
        invalid = self.client.get("/document-request/not-a-real-token")
        self.assertEqual(invalid.status_code, 404)
        self.assertIn(b"invalid or has expired", invalid.data)

        expired = self.create_request()
        expired.request.expires_at = datetime.now(timezone.utc).replace(
            tzinfo=None
        ) - timedelta(days=1)
        db.session.commit()

        expired_response = self.client.get(f"/document-request/{expired.token}")
        self.assertEqual(expired_response.status_code, 404)
        self.assertEqual(
            db.session.get(DocumentRequest, expired.request.id).status,
            DOCUMENT_REQUEST_EXPIRED,
        )

        cancelled = self.create_request()
        cancelled.request.status = DOCUMENT_REQUEST_CANCELLED
        db.session.commit()

        cancelled_response = self.client.get(
            f"/document-request/{cancelled.token}"
        )
        self.assertEqual(cancelled_response.status_code, 404)

    def test_upload_creates_normal_document_and_completes_request(self):
        delivery = self.create_request()

        with patch(
            "app.routes.document_requests.analyze_and_save_document",
            return_value={"success": True},
        ) as analyze:
            response = self.post_upload(delivery.token)

        self.assertEqual(response.status_code, 200)
        document = Document.query.one()
        request = DocumentRequest.query.one()
        self.assertEqual(document.sub_id, self.subcontractor_id)
        self.assertEqual(document.document_type, "COI")
        self.assertEqual(document.uploaded_by, self.owner_id)
        self.assertEqual(request.status, DOCUMENT_REQUEST_COMPLETED)
        self.assertEqual(request.document_id, document.id)
        analyze.assert_called_once_with(document.id)

        retry = self.client.get(f"/document-request/{delivery.token}")
        self.assertEqual(retry.status_code, 404)
        self.assertEqual(Document.query.count(), 1)

    def test_completed_request_allows_new_correction_request(self):
        delivery = self.create_request()

        with patch(
            "app.routes.document_requests.analyze_and_save_document",
            return_value={"success": True},
        ):
            self.post_upload(delivery.token)

        completed_id = delivery.request.id

        with patch(
            "app.services.document_requests.send_email_reminder",
            return_value=True,
        ):
            correction = create_or_resend_coi_request(
                organization=self.organization,
                project=self.project,
                subcontractor=self.subcontractor,
                created_by_user_id=self.owner_id,
            )

        self.assertEqual(DocumentRequest.query.count(), 2)
        self.assertNotEqual(correction.request.id, completed_id)
        self.assertEqual(correction.request.status, DOCUMENT_REQUEST_PENDING)
        self.assertEqual(self.client.get(f"/document-request/{correction.token}").status_code, 200)

    def test_completed_magic_link_cannot_create_second_document(self):
        delivery = self.create_request()
        csrf_token = self.csrf_token(f"/document-request/{delivery.token}")

        with patch(
            "app.routes.document_requests.analyze_and_save_document",
            return_value={"success": True},
        ) as analyze:
            first = self.client.post(
                f"/document-request/{delivery.token}",
                data={
                    "csrf_token": csrf_token,
                    "file": (BytesIO(b"%PDF-1.4 first"), "coi.pdf"),
                },
                content_type="multipart/form-data",
            )
            second = self.client.post(
                f"/document-request/{delivery.token}",
                data={
                    "csrf_token": csrf_token,
                    "file": (BytesIO(b"%PDF-1.4 second"), "coi.pdf"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 404)
        self.assertEqual(Document.query.count(), 1)
        analyze.assert_called_once()

    def test_public_upload_ignores_tampered_context_fields(self):
        delivery = self.create_request()
        csrf_token = self.csrf_token(f"/document-request/{delivery.token}")

        with patch(
            "app.routes.document_requests.analyze_and_save_document",
            return_value={"success": True},
        ):
            response = self.client.post(
                f"/document-request/{delivery.token}",
                data={
                    "csrf_token": csrf_token,
                    "organization_id": self.other_organization.id,
                    "project_id": 9999,
                    "subcontractor_id": self.other_subcontractor_id,
                    "document_type": "Contract",
                    "uploaded_by": self.other_user.id,
                    "file": (BytesIO(b"%PDF-1.4 coi"), "coi.pdf"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        document = Document.query.one()
        self.assertEqual(document.sub_id, self.subcontractor_id)
        self.assertIsNone(document.project_id)
        self.assertEqual(document.document_type, "COI")
        self.assertEqual(document.uploaded_by, self.owner_id)

    def test_invalid_and_oversized_uploads_are_rejected(self):
        delivery = self.create_request()
        csrf_token = self.csrf_token(f"/document-request/{delivery.token}")

        invalid = self.client.post(
            f"/document-request/{delivery.token}",
            data={
                "csrf_token": csrf_token,
                "file": (BytesIO(b"bad"), "coi.exe"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(Document.query.count(), 0)

        self.app.config["MAX_CONTENT_LENGTH"] = 128
        oversized = self.client.post(
            f"/document-request/{delivery.token}",
            data={
                "csrf_token": csrf_token,
                "file": (BytesIO(b"x" * 1024), "coi.pdf"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(Document.query.count(), 0)

    def test_analysis_failure_preserves_document_and_file(self):
        delivery = self.create_request()

        with patch(
            "app.routes.document_requests.analyze_and_save_document",
            side_effect=RuntimeError("ai unavailable"),
        ):
            response = self.post_upload(delivery.token)

        self.assertEqual(response.status_code, 200)
        document = Document.query.one()
        self.assertEqual(document.ai_status, "failed")
        self.assertEqual(
            document.ai_error,
            "Document analysis failed.",
        )
        self.assertEqual(DocumentRequest.query.one().status, DOCUMENT_REQUEST_COMPLETED)
        self.assertTrue(os.path.exists(os.path.join(self.uploads.name, document.filename)))

    def test_upload_can_feed_readiness_with_valid_evidence(self):
        delivery = self.create_request()

        def analyze_success(document_id):
            document = db.session.get(Document, document_id)
            document.ai_status = "analyzed"
            document.ai_extracted_data = {
                "document_type": "coi",
                "expiration_date": "2027-09-01",
                "coverage_limit": 2_000_000,
                "general_liability": {
                    "each_occurrence": 2_000_000,
                    "expiration_date": "2027-09-01",
                },
                "confidence": 0.95,
            }
            document.ai_compliance_result = {
                "is_coi": True,
                "validator": {"valid": True, "errors": []},
                "confidence": 0.95,
            }
            db.session.commit()
            return {"success": True}

        with patch(
            "app.routes.document_requests.analyze_and_save_document",
            side_effect=analyze_success,
        ):
            self.post_upload(delivery.token)

        decision = calculate_readiness(
            db.session.get(ProjectSubcontractor, self.project_subcontractor.id)
        )
        self.assertEqual(decision["status"], READY)

    def test_low_coverage_evidence_blocks_readiness(self):
        delivery = self.create_request()

        def analyze_low_coverage(document_id):
            document = db.session.get(Document, document_id)
            document.ai_status = "analyzed"
            document.ai_extracted_data = {
                "document_type": "coi",
                "expiration_date": "2027-09-01",
                "coverage_limit": 500_000,
                "general_liability": {
                    "each_occurrence": 500_000,
                    "expiration_date": "2027-09-01",
                },
                "confidence": 0.95,
            }
            document.ai_compliance_result = {
                "is_coi": True,
                "validator": {"valid": True, "errors": []},
                "confidence": 0.95,
            }
            db.session.commit()
            return {"success": True}

        with patch(
            "app.routes.document_requests.analyze_and_save_document",
            side_effect=analyze_low_coverage,
        ):
            self.post_upload(delivery.token)

        decision = calculate_readiness(
            db.session.get(ProjectSubcontractor, self.project_subcontractor.id)
        )
        self.assertEqual(decision["status"], BLOCKED)
        self.assertIn(
            "COVERAGE_INSUFFICIENT",
            [reason["code"] for reason in decision["reasons"]],
        )

    def test_resend_reuses_pending_request_and_rotates_token(self):
        first = self.create_request()
        first_id = first.request.id
        first_hash = first.request.token_hash

        second = self.create_request()

        self.assertEqual(DocumentRequest.query.count(), 1)
        self.assertEqual(second.request.id, first_id)
        self.assertNotEqual(second.request.token_hash, first_hash)
        self.assertEqual(self.client.get(f"/document-request/{first.token}").status_code, 404)
        self.assertEqual(self.client.get(f"/document-request/{second.token}").status_code, 200)

    def test_expired_request_rejects_post(self):
        delivery = self.create_request()
        csrf_token = self.csrf_token(f"/document-request/{delivery.token}")
        delivery.request.expires_at = datetime.now(timezone.utc).replace(
            tzinfo=None
        )
        db.session.commit()

        response = self.client.post(
            f"/document-request/{delivery.token}",
            data={
                "csrf_token": csrf_token,
                "file": (BytesIO(b"%PDF-1.4 coi"), "coi.pdf"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Document.query.count(), 0)

    def test_project_document_request_button_renders_status(self):
        delivery = self.create_request()
        self.login_owner()

        response = self.client.get(f"/project/{self.project_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Resend", response.data)
        self.assertIn(b"COI request sent", response.data)
        self.assertNotIn(delivery.token.encode(), response.data)


if __name__ == "__main__":
    unittest.main()
