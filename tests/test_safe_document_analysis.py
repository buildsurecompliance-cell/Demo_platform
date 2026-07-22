import os
import re
import tempfile
import unittest

from contextlib import contextmanager
from datetime import date, timedelta
from io import BytesIO
from unittest.mock import patch

from werkzeug.datastructures import FileStorage


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Document, Project, ProjectSubcontractor, Subcontractor, User
from app.services.document_analysis_service import analyze_and_save_document
from app.services.document_analysis_service import (
    _get_extracted_email,
    _get_extracted_phone,
)
from app.services.compliance_evidence_service import coi_evidence_from_document
from app.services.document_intelligence.engine import analyze_document_intelligence
from app.services.documents.storage import save_document_file
from app.services.organizations import create_default_organization_for_user
from app.services.readiness_service import BLOCKED, READY, calculate_readiness


@contextmanager
def temporary_analysis_path(_document):
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    temp_file.write(b"mock analysis file")
    temp_file.close()

    try:
        yield temp_file.name
    finally:
        os.unlink(temp_file.name)


class SafeDocumentAnalysisTest(unittest.TestCase):

    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory()
        self.app = create_app(TestingConfig)
        self.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=True,
            PROPAGATE_EXCEPTIONS=False,
            STORAGE_BACKEND="local",
            UPLOAD_FOLDER=self.uploads.name,
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
        self.uploads.cleanup()

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

    def _write_temp_document(self, content):
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        temp_file.write(content)
        temp_file.close()
        return temp_file.name

    def _file_storage(self, content, filename="Certificate_of_Insurance.pdf"):
        return FileStorage(
            stream=BytesIO(content),
            filename=filename,
            content_type="application/pdf",
        )

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
        email=None,
        phone=None,
        trade=None,
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
        if email is not None:
            extracted_data["email"] = email
        if phone is not None:
            extracted_data["phone"] = phone
        if trade is not None:
            extracted_data["trade"] = trade

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
        email=None,
        phone=None,
        role=None,
    ):
        subcontractor = Subcontractor(
            name=sub_name,
            user_id=self.user_id,
            organization_id=self.organization_id,
            coi_expiration=coi_expiration,
            email=email,
            phone=phone,
            role=role,
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
        content = b"""
Certificate of Insurance
Named Insured: Demo Concrete LLC
Carrier: Demo Mutual
Policy Number: GL-2000
Policy EFF: 01/01/2027
Policy EXP: 12/31/2027
Commercial General Liability
Each Occurrence USD 2,000,000
General Aggregate USD 4,000,000
Workers Compensation Statutory
TRADE / OPERATIONS: Concrete Contractor
SUBCONTRACTOR EMAIL: Contact@ApexConcrete.COM
SUBCONTRACTOR PHONE: (407) 555-0132
"""
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        temp_file.write(content)
        temp_file.close()

        with patch.dict(os.environ, {"AI_MOCK_MODE": "true"}):
            try:
                result = analyze_document_intelligence(
                    file_path=temp_file.name,
                    document_type="COI",
                )
            finally:
                os.unlink(temp_file.name)

        self.assertTrue(result["success"])
        self.assertEqual(result["category"], "coi")
        self.assertEqual(result["compliance"]["status"], "Ready")
        self.assertEqual(result["compliance"]["issues"], [])
        self.assertEqual(result["extracted_data"]["expiration_date"], "2027-12-31")
        self.assertEqual(
            result["extracted_data"]["general_liability_limit"],
            "2000000",
        )
        self.assertEqual(result["extracted_data"]["trade"], "Concrete Contractor")
        self.assertEqual(
            result["extracted_data"]["email"],
            "contact@apexconcrete.com",
        )
        self.assertEqual(result["extracted_data"]["phone"], "+14075550132")

    def test_contact_helpers_normalize_email_and_phone_legacy_keys(self):
        self.assertEqual(
            _get_extracted_email({"contact_email": " CONTACT@ApexConcrete.COM; "}),
            "contact@apexconcrete.com",
        )
        self.assertIsNone(_get_extracted_email({"email": "not-an-email"}))
        self.assertEqual(
            _get_extracted_phone({"phone_number": "(407) 555-0132"}),
            "+14075550132",
        )
        self.assertEqual(
            _get_extracted_phone({"telephone": "407-555-0132"}),
            "+14075550132",
        )
        self.assertEqual(
            _get_extracted_phone({"contact_phone": "+1 407 555 0132"}),
            "+14075550132",
        )
        self.assertIsNone(_get_extracted_phone({"phone": "123"}))

    def test_mock_mode_coi_reads_each_file_without_cross_document_contamination(self):
        first_path = self._write_temp_document(
            b"""
Certificate of Insurance
Policy EXP: 12/31/2027
Commercial General Liability
Each Occurrence $2,000,000
General Aggregate $4,000,000
TRADE / OPERATIONS: Concrete Contractor
"""
        )
        second_path = self._write_temp_document(
            b"""
Certificate of Insurance
Date Issued: 01/01/2029
Policy EXP: 06/30/2028
Commercial General Liability
Each Occurrence $1,000,000
General Aggregate $9,000,000
TYPE OF WORK: Roofing Contractor
"""
        )

        try:
            with patch.dict(os.environ, {"AI_MOCK_MODE": "true"}):
                first = analyze_document_intelligence(first_path, "COI")
                second = analyze_document_intelligence(second_path, "COI")
                second_again = analyze_document_intelligence(second_path, "COI")
                first_again = analyze_document_intelligence(first_path, "COI")
        finally:
            os.unlink(first_path)
            os.unlink(second_path)

        self.assertEqual(
            first["extracted_data"]["general_liability_limit"],
            "2000000",
        )
        self.assertEqual(first["extracted_data"]["expiration_date"], "2027-12-31")
        self.assertEqual(
            second["extracted_data"]["general_liability_limit"],
            "1000000",
        )
        self.assertEqual(second["extracted_data"]["expiration_date"], "2028-06-30")
        self.assertEqual(second["extracted_data"], second_again["extracted_data"])
        self.assertEqual(first["extracted_data"], first_again["extracted_data"])

    def test_mock_mode_coi_fails_cleanly_for_invalid_file(self):
        path = self._write_temp_document(b"not a certificate")

        try:
            with patch.dict(os.environ, {"AI_MOCK_MODE": "true"}):
                result = analyze_document_intelligence(path, "COI")
        finally:
            os.unlink(path)

        self.assertFalse(result["success"])
        self.assertEqual(result["extracted_data"], {})
        self.assertEqual(result["compliance"], {})

    def test_mock_mode_coi_ignores_producer_contact_details(self):
        path = self._write_temp_document(
            b"""
Certificate of Insurance
Producer Email: producer@example.com
Producer Phone: (999) 555-0199
Policy EXP: 12/31/2027
Commercial General Liability
Each Occurrence $2,000,000
"""
        )

        try:
            with patch.dict(os.environ, {"AI_MOCK_MODE": "true"}):
                result = analyze_document_intelligence(path, "COI")
        finally:
            os.unlink(path)

        self.assertTrue(result["success"])
        self.assertIsNone(result["extracted_data"]["email"])
        self.assertIsNone(result["extracted_data"]["phone"])

    def test_mock_mode_coi_extracts_unlabeled_contact_inside_insured_block(self):
        path = self._write_temp_document(
            b"""
Certificate of Insurance
Named Insured
Apex Concrete LLC
contact@apexconcrete.com
(407) 555-0132
Policy EXP: 12/31/2027
Commercial General Liability
Each Occurrence $2,000,000
"""
        )

        try:
            with patch.dict(os.environ, {"AI_MOCK_MODE": "true"}):
                result = analyze_document_intelligence(path, "COI")
        finally:
            os.unlink(path)

        self.assertTrue(result["success"])
        self.assertEqual(result["extracted_data"]["email"], "contact@apexconcrete.com")
        self.assertEqual(result["extracted_data"]["phone"], "+14075550132")

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

    def test_valid_coi_analysis_accepts_expiration_and_coverage_aliases(self):
        with self.app.app_context():
            subcontractor_id, document_id = self.make_subcontractor_and_document()
            analysis_result = self.valid_coi_analysis_result()
            analysis_result["extracted_data"].pop("expiration_date")
            analysis_result["extracted_data"].pop("general_liability_limit")
            analysis_result["extracted_data"]["policy_exp"] = "09/01/2027"
            analysis_result["extracted_data"][
                "general_liability_each_occurrence"
            ] = "$2,500,000"

            result = self.analyze_with_mock_result(document_id, analysis_result)

            subcontractor = db.session.get(Subcontractor, subcontractor_id)
            document = db.session.get(Document, document_id)
            evidence = coi_evidence_from_document(document)

            self.assertTrue(result["success"])
            self.assertEqual(
                document.ai_extracted_data["policy_exp"],
                "09/01/2027",
            )
            self.assertEqual(
                document.ai_extracted_data["general_liability_each_occurrence"],
                "$2,500,000",
            )
            self.assertTrue(evidence.validated)
            self.assertEqual(
                evidence.value["expiration_date"].isoformat(),
                "2027-09-01",
            )
            self.assertEqual(evidence.value["coverage"], 2500000)
            self.assertEqual(
                subcontractor.coi_expiration.isoformat(),
                "2027-09-01",
            )

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

    def test_valid_coi_analysis_fills_missing_subcontractor_contact(self):
        with self.app.app_context():
            subcontractor_id, document_id = self.make_subcontractor_and_document()

            self.analyze_with_mock_result(
                document_id,
                self.valid_coi_analysis_result(
                    email=" CONTACT@ApexConcrete.COM ",
                    phone="(407) 555-0132",
                ),
            )

            subcontractor = db.session.get(Subcontractor, subcontractor_id)

            self.assertEqual(subcontractor.email, "contact@apexconcrete.com")
            self.assertEqual(subcontractor.phone, "+14075550132")

    def test_valid_coi_analysis_does_not_overwrite_manual_contact(self):
        with self.app.app_context():
            subcontractor_id, document_id = self.make_subcontractor_and_document(
                email="owner@apexconcrete.com",
                phone="+14075559999",
            )

            self.analyze_with_mock_result(
                document_id,
                self.valid_coi_analysis_result(
                    email="new@apexconcrete.com",
                    phone="407-555-0132",
                ),
            )

            subcontractor = db.session.get(Subcontractor, subcontractor_id)

            self.assertEqual(subcontractor.email, "owner@apexconcrete.com")
            self.assertEqual(subcontractor.phone, "+14075559999")

    def test_analysis_without_contact_does_not_clear_existing_contact(self):
        with self.app.app_context():
            subcontractor_id, document_id = self.make_subcontractor_and_document(
                email="owner@apexconcrete.com",
                phone="+14075559999",
            )

            self.analyze_with_mock_result(
                document_id,
                self.valid_coi_analysis_result(),
            )

            subcontractor = db.session.get(Subcontractor, subcontractor_id)

            self.assertEqual(subcontractor.email, "owner@apexconcrete.com")
            self.assertEqual(subcontractor.phone, "+14075559999")

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

    def test_reanalyzing_new_same_name_coi_uses_current_file_and_updates_readiness(self):
        with self.app.app_context(), patch.dict(os.environ, {"AI_MOCK_MODE": "true"}):
            project = Project(
                name="Coverage Project",
                user_id=self.user_id,
                organization_id=self.organization_id,
                required_coverage=2_000_000,
            )
            subcontractor = Subcontractor(
                name="Apex Concrete",
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            db.session.add_all([project, subcontractor])
            db.session.flush()
            link = ProjectSubcontractor(
                project_id=project.id,
                subcontractor_id=subcontractor.id,
                coverage_limit=2_000_000,
            )
            db.session.add(link)
            db.session.commit()

            first_key = save_document_file(
                self._file_storage(
                    b"""
Certificate of Insurance
Policy EXP: 12/31/2027
Commercial General Liability
Each Occurrence $2,000,000
General Aggregate $4,000,000
TRADE / OPERATIONS: Concrete Contractor
SUBCONTRACTOR EMAIL: contact@apexconcrete.com
SUBCONTRACTOR PHONE: (407) 555-0132
Workers Compensation Statutory
""",
                ),
                "Certificate_of_Insurance.pdf",
                sub_id=subcontractor.id,
            )
            first_doc = Document(
                filename=first_key,
                original_name="Certificate_of_Insurance.pdf",
                document_type="COI",
                sub_id=subcontractor.id,
                uploaded_by=self.user_id,
            )
            db.session.add(first_doc)
            db.session.commit()

            first_result = analyze_and_save_document(first_doc.id)

            self.assertTrue(first_result["success"])
            self.assertRegex(
                first_doc.filename,
                rf"^subcontractors/{subcontractor.id}/[a-f0-9]{{32}}\.pdf$",
            )
            self.assertEqual(first_doc.original_name, "Certificate_of_Insurance.pdf")
            self.assertEqual(
                first_doc.ai_extracted_data["general_liability_limit"],
                "2000000",
            )
            self.assertEqual(
                first_doc.ai_extracted_data["email"],
                "contact@apexconcrete.com",
            )
            self.assertEqual(first_doc.ai_extracted_data["phone"], "+14075550132")
            self.assertEqual(subcontractor.role, "Concrete Contractor")
            self.assertEqual(subcontractor.email, "contact@apexconcrete.com")
            self.assertEqual(subcontractor.phone, "+14075550132")
            self.assertEqual(
                calculate_readiness(link)["status"],
                READY,
            )

            subcontractor.email = "owner@apexconcrete.com"
            subcontractor.phone = "+14075559999"
            db.session.commit()

            second_key = save_document_file(
                self._file_storage(
                    b"""
Certificate of Insurance
Date Issued: 01/01/2029
Policy EXP: 06/30/2028
Commercial General Liability
Each Occurrence $1,000,000
General Aggregate $9,000,000
TYPE OF WORK: Roofing Contractor
SUBCONTRACTOR EMAIL: replacement@apexconcrete.com
SUBCONTRACTOR PHONE: +1 407 555 7777
Workers Compensation Statutory
""",
                ),
                "Certificate_of_Insurance.pdf",
                sub_id=subcontractor.id,
            )
            second_doc = Document(
                filename=second_key,
                original_name="Certificate_of_Insurance.pdf",
                document_type="COI",
                sub_id=subcontractor.id,
                uploaded_by=self.user_id,
            )
            db.session.add(second_doc)
            db.session.commit()

            second_result = analyze_and_save_document(second_doc.id)
            readiness = calculate_readiness(link)

            self.assertTrue(second_result["success"])
            self.assertNotEqual(first_doc.filename, second_doc.filename)
            self.assertEqual(
                second_doc.ai_extracted_data["general_liability_limit"],
                "1000000",
            )
            self.assertEqual(
                second_doc.ai_extracted_data["expiration_date"],
                "2028-06-30",
            )
            self.assertEqual(
                second_doc.ai_extracted_data["email"],
                "replacement@apexconcrete.com",
            )
            self.assertEqual(second_doc.ai_extracted_data["phone"], "+14075557777")
            self.assertEqual(subcontractor.coi_expiration.isoformat(), "2028-06-30")
            self.assertEqual(subcontractor.role, "Concrete Contractor")
            self.assertEqual(subcontractor.email, "owner@apexconcrete.com")
            self.assertEqual(subcontractor.phone, "+14075559999")
            self.assertEqual(readiness["status"], BLOCKED)
            self.assertTrue(
                any(
                    reason["code"] == "COVERAGE_INSUFFICIENT"
                    for reason in readiness["reasons"]
                )
            )


if __name__ == "__main__":
    unittest.main()
