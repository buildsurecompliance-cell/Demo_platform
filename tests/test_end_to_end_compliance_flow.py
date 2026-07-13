import os
import tempfile
import unittest

from datetime import date, timedelta
from io import BytesIO
from unittest.mock import patch

from flask import Response

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.extensions import db
from app.models import Document, Project, ProjectSubcontractor, Subcontractor, User
from app.services.compliance_officer import generate_compliance_advice
from app.services.compliance_profiles import evaluate_profile_requirements
from app.services.readiness_service import BLOCKED, PENDING, READY, calculate_readiness
from app.services.documents.storage import StorageError


class EndToEndComplianceFlowTest(unittest.TestCase):

    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory()
        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
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
            db.session.commit()
            self.user_id = self.user.id
            self.other_user_id = self.other_user.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

        self.uploads.cleanup()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True

    def create_project(self, name="Audit Project", user_id=None):
        project = Project(
            name=name,
            user_id=user_id or self.user_id,
        )
        db.session.add(project)
        db.session.flush()
        return project

    def create_subcontractor(
        self,
        name="Audit Sub",
        user_id=None,
        coi_expiration=None,
        role="Concrete",
    ):
        subcontractor = Subcontractor(
            name=name,
            user_id=user_id or self.user_id,
            role=role,
            coi_expiration=coi_expiration,
        )
        db.session.add(subcontractor)
        db.session.flush()
        return subcontractor

    def link(self, project, subcontractor, coverage_limit=1000000):
        project_subcontractor = ProjectSubcontractor(
            project_id=project.id,
            subcontractor_id=subcontractor.id,
            coverage_limit=coverage_limit,
        )
        db.session.add(project_subcontractor)
        db.session.flush()
        return project_subcontractor

    def create_coi_document(
        self,
        subcontractor,
        *,
        ai_status="analyzed",
        confidence=0.92,
        expiration_date=None,
        coverage=1000000,
        issues=None,
        compliance_status="Ready",
        extracted_data=None,
        compliance_result=None,
    ):
        expiration_date = expiration_date or (
            date.today() + timedelta(days=60)
        ).isoformat()

        document = Document(
            filename="coi.pdf",
            original_name="coi.pdf",
            document_type="COI",
            version=1,
            sub_id=subcontractor.id,
            uploaded_by=subcontractor.user_id,
            ai_status=ai_status,
            ai_confidence=confidence,
            ai_extracted_data=(
                extracted_data
                if extracted_data is not None
                else {
                    "expiration_date": expiration_date,
                    "general_liability_limit": coverage,
                    "insurance_carrier": "Sample Carrier",
                    "policy_number": "POL-123",
                    "confidence": confidence,
                }
            ),
            ai_compliance_result=(
                compliance_result
                if compliance_result is not None
                else {
                    "status": compliance_status,
                    "issues": issues or [],
                    "warnings": [],
                    "confidence": confidence,
                }
            ),
        )
        db.session.add(document)
        db.session.flush()
        return document

    def render_project(self, project_id):
        self.login(self.user_id)

        with patch(
            "app.routes.projects.get_project_ai_summary",
            return_value=None,
        ):
            return self.client.get(f"/project/{project_id}")

    def assert_view_status(self, project_id, status, summary):
        response = self.render_project(project_id)
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(status, body)
        self.assertIn(summary, body)
        return body

    def test_blocked_missing_coi_flows_to_project_view(self):
        with self.app.app_context():
            project = self.create_project()
            subcontractor = self.create_subcontractor(coi_expiration=None)
            project_subcontractor = self.link(project, subcontractor)
            db.session.commit()

            evaluation = evaluate_profile_requirements(project_subcontractor)
            readiness = calculate_readiness(project_subcontractor)
            advice = generate_compliance_advice(project_subcontractor)

            self.assertEqual(evaluation["requirements"][0]["status"], "MISSING")
            self.assertEqual(readiness["status"], BLOCKED)
            self.assertEqual(project.mobilization_status, "Blocked")
            self.assertEqual(advice.status, BLOCKED)
            self.assertIn("Certificate of Insurance is missing", advice.summary)
            self.assertIn(
                "Upload a valid Certificate of Insurance.",
                [action.title for action in advice.actions],
            )
            project_id = project.id

        body = self.assert_view_status(
            project_id,
            "BLOCKED",
            "Mobilization blocked because a Certificate of Insurance is missing.",
        )
        self.assertIn("Upload a valid Certificate of Insurance.", body)

    def test_blocked_expired_coi_has_single_reason_and_view_matches(self):
        with self.app.app_context():
            project = self.create_project()
            subcontractor = self.create_subcontractor(
                coi_expiration=date.today() - timedelta(days=1),
            )
            project_subcontractor = self.link(project, subcontractor)
            db.session.commit()

            evaluation = evaluate_profile_requirements(project_subcontractor)
            readiness = calculate_readiness(project_subcontractor)
            advice = generate_compliance_advice(project_subcontractor)
            reason_codes = [
                reason["code"]
                for reason in readiness["reasons"]
            ]

            self.assertEqual(evaluation["requirements"][0]["status"], "INVALID")
            self.assertEqual(readiness["status"], BLOCKED)
            self.assertEqual(reason_codes.count("COI_EXPIRED"), 1)
            self.assertIn(
                "Request a renewed insurance certificate.",
                [action.title for action in advice.actions],
            )
            project_id = project.id

        body = self.assert_view_status(
            project_id,
            "BLOCKED",
            "Mobilization blocked because the Certificate of Insurance expired.",
        )
        self.assertNotIn("READY", body)

    def test_pending_cases_flow_to_view_without_ready_language(self):
        cases = [
            (
                "expires_today",
                {"manual_expiration": date.today()},
                "COI_EXPIRING_SOON",
            ),
            (
                "expires_in_30_days",
                {"manual_expiration": date.today() + timedelta(days=30)},
                "COI_EXPIRING_SOON",
            ),
            (
                "processing",
                {"document": {"ai_status": "not_analyzed"}},
                "COI_DOCUMENT_PARTIAL",
            ),
            (
                "unreadable",
                {"document": {"ai_status": "failed"}},
                "COI_DOCUMENT_UNREADABLE",
            ),
            (
                "low_confidence",
                {"document": {"confidence": 0.5}},
                "AI_CONFIDENCE_LOW",
            ),
            (
                "validator_failed",
                {"document": {"issues": [{"message": "Missing date."}]}},
                "AI_VALIDATION_FAILED",
            ),
        ]

        for name, setup, reason_code in cases:
            with self.subTest(name=name):
                with self.app.app_context():
                    db.session.remove()
                    db.drop_all()
                    db.create_all()

                    user = User(email=f"{name}@example.com", paid=True)
                    user.set_password("password123")
                    db.session.add(user)
                    db.session.flush()
                    self.user_id = user.id

                    project = self.create_project(
                        name=f"Project {name}",
                        user_id=user.id,
                    )
                    subcontractor = self.create_subcontractor(
                        name=f"Sub {name}",
                        user_id=user.id,
                        coi_expiration=setup.get("manual_expiration"),
                    )
                    project_subcontractor = self.link(project, subcontractor)

                    if "document" in setup:
                        self.create_coi_document(
                            subcontractor,
                            **setup["document"],
                        )

                    db.session.commit()
                    readiness = calculate_readiness(project_subcontractor)
                    advice = generate_compliance_advice(project_subcontractor)
                    project_id = project.id

                    self.assertEqual(readiness["status"], PENDING)
                    self.assertIn(
                        reason_code,
                        {
                            reason["code"]
                            for reason in readiness["reasons"]
                        },
                    )
                    self.assertEqual(project.mobilization_status, "Pending Compliance")
                    self.assertNotIn("Ready for mobilization.", advice.summary)
                    self.assertTrue(advice.actions)

                body = self.assert_view_status(
                    project_id,
                    "PENDING",
                    advice.summary,
                )
                self.assertNotIn("Ready for mobilization.", body)

    def test_ready_flow_has_satisfied_profile_empty_actions_and_no_empty_list(self):
        with self.app.app_context():
            project = self.create_project()
            subcontractor = self.create_subcontractor(coi_expiration=None)
            project_subcontractor = self.link(project, subcontractor)
            self.create_coi_document(subcontractor)
            db.session.commit()

            evaluation = evaluate_profile_requirements(project_subcontractor)
            readiness = calculate_readiness(project_subcontractor)
            advice = generate_compliance_advice(project_subcontractor)

            self.assertEqual(evaluation["requirements"][0]["status"], "SATISFIED")
            self.assertEqual(readiness["status"], READY)
            self.assertEqual(project.mobilization_status, "Ready to Mobilize")
            self.assertEqual(advice.summary, "Ready for mobilization.")
            self.assertEqual(advice.actions, ())
            project_id = project.id

        body = self.assert_view_status(
            project_id,
            "READY",
            "Ready for mobilization.",
        )
        self.assertNotIn("<ul class=\"mb-0\">", body)

    def test_malformed_ai_data_stays_pending_and_view_renders(self):
        with self.app.app_context():
            project = self.create_project()
            subcontractor = self.create_subcontractor()
            project_subcontractor = self.link(project, subcontractor)
            self.create_coi_document(
                subcontractor,
                extracted_data="not-json",
                compliance_result="not-json",
            )
            db.session.commit()

            readiness = calculate_readiness(project_subcontractor)
            advice = generate_compliance_advice(project_subcontractor)
            project_id = project.id

            self.assertEqual(readiness["status"], PENDING)
            self.assertIn("could not be validated", advice.summary)

        self.assert_view_status(project_id, "PENDING", advice.summary)

    def test_multiple_subcontractors_have_independent_advice_and_block_project(self):
        with self.app.app_context():
            project = self.create_project()
            ready_sub = self.create_subcontractor("Ready Sub")
            pending_sub = self.create_subcontractor(
                "Pending Sub",
                coi_expiration=date.today() + timedelta(days=30),
            )
            blocked_sub = self.create_subcontractor("Blocked Sub")
            ready_link = self.link(project, ready_sub)
            pending_link = self.link(project, pending_sub)
            blocked_link = self.link(project, blocked_sub)
            self.create_coi_document(ready_sub)
            db.session.commit()

            self.assertEqual(calculate_readiness(ready_link)["status"], READY)
            self.assertEqual(calculate_readiness(pending_link)["status"], PENDING)
            self.assertEqual(calculate_readiness(blocked_link)["status"], BLOCKED)
            self.assertEqual(project.mobilization_status, "Blocked")
            self.assertEqual(project.compliance_score, 33)
            project_id = project.id

        body = self.assert_view_status(project_id, "BLOCKED", "Mobilization blocked")
        self.assertIn("Ready Sub", body)
        self.assertIn("Pending Sub", body)
        self.assertIn("Blocked Sub", body)
        self.assertLess(body.index("Ready Sub"), body.index("Pending Sub"))
        self.assertLess(body.index("Pending Sub"), body.index("Blocked Sub"))

    def test_add_project_ignores_subcontractor_owned_by_another_user(self):
        with self.app.app_context():
            owned_sub = self.create_subcontractor("Owned Sub")
            other_sub = self.create_subcontractor(
                "Other Sub",
                user_id=self.other_user_id,
            )
            db.session.commit()
            owned_sub_id = owned_sub.id
            other_sub_id = other_sub.id

        self.login(self.user_id)
        response = self.client.post(
            "/add_project",
            data={
                "name": "Linked Project",
                "contract_value": "100",
                "subcontractors": [
                    str(owned_sub_id),
                    str(other_sub_id),
                    "not-an-id",
                ],
            },
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            project = Project.query.filter_by(name="Linked Project").one()
            links = ProjectSubcontractor.query.filter_by(
                project_id=project.id,
            ).all()

            self.assertEqual(len(links), 1)
            self.assertEqual(links[0].subcontractor_id, owned_sub_id)

    def test_project_document_upload_view_download_delete_and_legacy_path(self):
        with self.app.app_context():
            project = self.create_project()
            db.session.commit()
            project_id = project.id

        self.login(self.user_id)

        for doc_type in ("Contract", "Scope", "Owner Requirements"):
            response = self.client.post(
                f"/project/{project_id}/upload",
                data={
                    "doc_type": doc_type,
                    "file": (
                        BytesIO(b"%PDF-1.4\nproject document"),
                        f"{doc_type.lower().replace(' ', '_')}.pdf",
                    ),
                },
                content_type="multipart/form-data",
            )
            self.assertEqual(response.status_code, 302)

        response = self.client.post(
            f"/project/{project_id}/upload",
            data={
                "doc_type": "Contract",
                "file": (BytesIO(b"%PDF-1.4\ncontract v2"), "contract_v2.pdf"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            docs = Document.query.filter_by(project_id=project_id).all()
            self.assertEqual(len(docs), 4)
            contract_versions = sorted(
                doc.version
                for doc in docs
                if doc.document_type == "Contract"
            )
            self.assertEqual(contract_versions, [1, 2])
            newest_contract = Document.query.filter_by(
                project_id=project_id,
                document_type="Contract",
                version=2,
            ).one()
            new_path = os.path.join(
                self.uploads.name,
                *newest_contract.filename.split("/"),
            )
            self.assertTrue(os.path.exists(new_path))
            newest_contract_id = newest_contract.id

            legacy_path = os.path.join(self.uploads.name, "legacy_scope.pdf")
            with open(legacy_path, "wb") as legacy_file:
                legacy_file.write(b"%PDF-1.4\nlegacy")
            legacy_doc = Document(
                filename="legacy_scope.pdf",
                original_name="legacy_scope.pdf",
                document_type="Scope",
                version=99,
                project_id=project_id,
            )
            db.session.add(legacy_doc)
            db.session.commit()
            legacy_doc_id = legacy_doc.id

        view_response = self.client.get(f"/document/{newest_contract_id}")
        try:
            self.assertEqual(view_response.status_code, 200)
        finally:
            view_response.close()

        download_response = self.client.get(
            f"/download_document/{newest_contract_id}"
        )
        try:
            self.assertEqual(download_response.status_code, 200)
        finally:
            download_response.close()

        legacy_response = self.client.get(f"/document/{legacy_doc_id}")
        try:
            self.assertEqual(legacy_response.status_code, 200)
        finally:
            legacy_response.close()

        response = self.client.post(
            f"/delete_document/{newest_contract_id}",
            headers={"Referer": f"/project/{project_id}"},
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            self.assertIsNone(db.session.get(Document, newest_contract_id))
            self.assertFalse(os.path.exists(new_path))

    def test_user_cannot_access_or_delete_other_users_project_document(self):
        with self.app.app_context():
            other_project = self.create_project(
                name="Other Project",
                user_id=self.other_user_id,
            )
            db.session.commit()
            project_id = other_project.id
            os.makedirs(
                os.path.join(self.uploads.name, f"project_{project_id}"),
                exist_ok=True,
            )
            document_path = os.path.join(
                self.uploads.name,
                f"project_{project_id}",
                "other.pdf",
            )
            with open(document_path, "wb") as document_file:
                document_file.write(b"%PDF-1.4\nother")
            document = Document(
                filename="other.pdf",
                original_name="other.pdf",
                document_type="Contract",
                project_id=project_id,
            )
            db.session.add(document)
            db.session.commit()
            document_id = document.id

        self.login(self.user_id)

        view_response = self.client.get(f"/document/{document_id}")
        download_response = self.client.get(f"/download_document/{document_id}")
        delete_response = self.client.post(f"/delete_document/{document_id}")

        self.assertEqual(view_response.status_code, 302)
        self.assertEqual(download_response.status_code, 302)
        self.assertEqual(delete_response.status_code, 302)

        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Document, document_id))
            self.assertTrue(os.path.exists(document_path))

    def test_project_upload_storage_failure_does_not_create_document(self):
        with self.app.app_context():
            project = self.create_project()
            db.session.commit()
            project_id = project.id

        self.login(self.user_id)

        with patch(
            "app.routes.projects.save_document_file",
            side_effect=StorageError("storage unavailable"),
        ):
            response = self.client.post(
                f"/project/{project_id}/upload",
                data={
                    "doc_type": "Contract",
                    "file": (BytesIO(b"%PDF-1.4\ncontract"), "contract.pdf"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            self.assertEqual(
                Document.query.filter_by(project_id=project_id).count(),
                0,
            )

    def test_project_upload_db_failure_cleans_saved_document(self):
        with self.app.app_context():
            project = self.create_project()
            db.session.commit()
            project_id = project.id

        self.login(self.user_id)

        with patch(
            "app.routes.projects.db.session.commit",
            side_effect=Exception("database unavailable"),
        ):
            response = self.client.post(
                f"/project/{project_id}/upload",
                data={
                    "doc_type": "Contract",
                    "file": (BytesIO(b"%PDF-1.4\ncontract"), "contract.pdf"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            self.assertEqual(
                Document.query.filter_by(project_id=project_id).count(),
                0,
            )
            uploaded_path = os.path.join(
                self.uploads.name,
                "projects",
                str(project_id),
            )
            uploaded_files = (
                os.listdir(uploaded_path)
                if os.path.exists(uploaded_path)
                else []
            )
            self.assertEqual(uploaded_files, [])

    def test_project_upload_same_filename_generates_distinct_storage_keys(self):
        with self.app.app_context():
            project = self.create_project()
            db.session.commit()
            project_id = project.id

        self.login(self.user_id)

        for content in [b"%PDF-1.4\nfirst", b"%PDF-1.4\nsecond"]:
            response = self.client.post(
                f"/project/{project_id}/upload",
                data={
                    "doc_type": "Contract",
                    "file": (BytesIO(content), "same_name.pdf"),
                },
                content_type="multipart/form-data",
            )
            self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            docs = (
                Document.query
                .filter_by(project_id=project_id, document_type="Contract")
                .order_by(Document.version)
                .all()
            )

            self.assertEqual([doc.version for doc in docs], [1, 2])
            self.assertNotEqual(docs[0].filename, docs[1].filename)
            self.assertTrue(docs[0].filename.startswith(f"projects/{project_id}/"))
            self.assertTrue(docs[1].filename.startswith(f"projects/{project_id}/"))

    def test_view_and_download_use_document_storage_backend(self):
        with self.app.app_context():
            project = self.create_project()
            document = Document(
                filename="projects/1/stored.pdf",
                original_name="stored.pdf",
                document_type="Contract",
                project_id=project.id,
            )
            db.session.add(document)
            db.session.commit()
            document_id = document.id

        self.login(self.user_id)

        with patch(
            "app.routes.documents.document_exists",
            return_value=True,
        ) as exists_mock, patch(
            "app.routes.documents.get_document_response",
            side_effect=[
                Response("view"),
                Response("download"),
            ],
        ) as response_mock:
            view_response = self.client.get(f"/document/{document_id}")
            download_response = self.client.get(
                f"/download_document/{document_id}"
            )

        self.assertEqual(view_response.status_code, 200)
        self.assertEqual(download_response.status_code, 200)
        self.assertEqual(exists_mock.call_count, 2)
        self.assertEqual(response_mock.call_count, 2)
        self.assertFalse(response_mock.call_args_list[0].kwargs)
        self.assertTrue(response_mock.call_args_list[1].kwargs["as_attachment"])

    def test_unauthorized_delete_does_not_call_storage_backend(self):
        with self.app.app_context():
            other_project = self.create_project(
                name="Other Project",
                user_id=self.other_user_id,
            )
            document = Document(
                filename="projects/22/other.pdf",
                original_name="other.pdf",
                document_type="Contract",
                project_id=other_project.id,
            )
            db.session.add(document)
            db.session.commit()
            document_id = document.id

        self.login(self.user_id)

        with patch("app.routes.documents.delete_document_file") as delete_mock:
            response = self.client.post(f"/delete_document/{document_id}")

        self.assertEqual(response.status_code, 302)
        delete_mock.assert_not_called()

    def test_document_analysis_route_persists_ai_fields_without_real_ai(self):
        with self.app.app_context():
            project = self.create_project()
            subcontractor = self.create_subcontractor()
            self.link(project, subcontractor)
            document = Document(
                filename="coi_to_analyze.pdf",
                original_name="coi_to_analyze.pdf",
                document_type="COI",
                sub_id=subcontractor.id,
                uploaded_by=self.user_id,
            )
            db.session.add(document)
            db.session.commit()
            document_id = document.id
            file_path = os.path.join(self.uploads.name, document.filename)
            with open(file_path, "wb") as physical_file:
                physical_file.write(b"%PDF-1.4\ncoi")

        self.login(self.user_id)

        with patch(
            "app.services.document_analysis_service.analyze_document_intelligence",
            return_value={
                "success": True,
                "error": None,
                "category": "coi",
                "extracted_data": {
                    "expiration_date": (
                        date.today() + timedelta(days=60)
                    ).isoformat(),
                    "general_liability_limit": 1000000,
                    "insurance_carrier": "Sample Carrier",
                    "policy_number": "POL-123",
                    "confidence": 0.92,
                },
                "compliance": {
                    "status": "Ready",
                    "issues": [],
                    "warnings": [],
                    "confidence": 0.92,
                },
            },
        ):
            response = self.client.get(f"/documents/{document_id}/analyze")

        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            document = db.session.get(Document, document_id)
            self.assertEqual(document.ai_status, "analyzed")
            self.assertEqual(document.ai_confidence, 0.92)
            self.assertEqual(document.ai_extracted_data["policy_number"], "POL-123")
            self.assertEqual(document.ai_compliance_result["status"], "Ready")


if __name__ == "__main__":
    unittest.main()
