import os
import tempfile
import unittest

from contextlib import contextmanager
from datetime import date, timedelta
from io import BytesIO
from unittest.mock import patch
from sqlalchemy.orm.attributes import flag_modified

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Document, Project, ProjectSubcontractor, Subcontractor, User
from app.services.document_analysis_service import analyze_and_save_document
from app.services.document_intelligence.engine import analyze_document_intelligence
from app.services.organizations import create_default_organization_for_user
from app.services.projects.contract_autofill_service import (
    normalize_contract_extraction,
)
from app.services.readiness_service import BLOCKED, READY, calculate_readiness


@contextmanager
def temporary_analysis_path(doc):
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp_file:
        temp_file.write(b"%PDF-1.4\ncontract")
        temp_path = temp_file.name

    try:
        yield temp_path
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


class ProjectContractAutoFillTest(unittest.TestCase):

    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory()
        self.app = create_app(TestingConfig)
        self.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            UPLOAD_FOLDER=self.uploads.name,
            RATELIMIT_ENABLED=False,
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

    def login(self, user_id=None, organization_id=None):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id or self.user_id)
            session["_fresh"] = True
            session["organization_id"] = organization_id or self.organization_id

    def make_project(
        self,
        *,
        name="",
        contract_value=0,
        start_date=None,
        end_date=None,
        required_coverage=None,
        user_id=None,
        organization_id=None,
    ):
        project = Project(
            name=name,
            contract_value=contract_value,
            start_date=start_date,
            end_date=end_date,
            required_coverage=required_coverage,
            user_id=user_id or self.user_id,
            organization_id=organization_id or self.organization_id,
        )
        db.session.add(project)
        db.session.flush()
        return project

    def make_project_document(self, project, document_type="Contract"):
        document = Document(
            filename="projects/1/contract.pdf",
            original_name="contract.pdf",
            document_type=document_type,
            project_id=project.id,
            uploaded_by=project.user_id,
        )
        db.session.add(document)
        db.session.commit()
        return document.id

    def valid_contract_result(
        self,
        *,
        confidence=0.95,
        field_confidence=None,
        compliance_status="Ready",
        issues=None,
        data=None,
    ):
        extracted_data = {
            "document_type": "contract",
            "project_name": "Riverside Office Building",
            "contract_value": 4850000,
            "start_date": "2026-08-01",
            "end_date": "2027-06-30",
            "required_coverage": 2000000,
            "confidence": confidence,
            "field_confidence": {
                "project_name": 0.94,
                "contract_value": 0.97,
                "start_date": 0.93,
                "end_date": 0.91,
                "required_coverage": 0.90,
            },
            "notes": [],
        }

        if field_confidence:
            extracted_data["field_confidence"].update(field_confidence)

        if data:
            extracted_data.update(data)

        return {
            "success": True,
            "error": None,
            "category": "contract",
            "extracted_data": normalize_contract_extraction(extracted_data),
            "compliance": {
                "status": compliance_status,
                "issues": issues or [],
                "warnings": [],
                "confidence": confidence,
            },
        }

    def analyze_with_result(self, document_id, result):
        with patch(
            "app.services.document_analysis_service.temporary_document_path",
            new=temporary_analysis_path,
        ), patch(
            "app.services.document_analysis_service.analyze_document_intelligence",
            return_value=result,
        ):
            return analyze_and_save_document(document_id)

    def test_empty_project_valid_contract_fills_all_fields(self):
        with self.app.app_context():
            project = self.make_project()
            document_id = self.make_project_document(project)

            result = self.analyze_with_result(
                document_id,
                self.valid_contract_result(),
            )

            self.assertTrue(result["success"])
            project = db.session.get(Project, project.id)
            self.assertEqual(project.name, "Riverside Office Building")
            self.assertEqual(project.contract_value, 4850000)
            self.assertEqual(project.start_date.isoformat(), "2026-08-01")
            self.assertEqual(project.end_date.isoformat(), "2027-06-30")
            self.assertEqual(project.required_coverage, 2000000)

            document = db.session.get(Document, document_id)
            fields = document.ai_compliance_result["project_auto_fill"]["fields"]
            self.assertEqual(fields["project_name"]["status"], "applied")
            self.assertEqual(fields["required_coverage"]["status"], "applied")

    def test_each_field_can_fill_independently(self):
        with self.app.app_context():
            project = self.make_project(
                name="Untitled Project",
                contract_value=0,
                start_date=date(2026, 8, 1),
                end_date=None,
                required_coverage=None,
            )
            document_id = self.make_project_document(project)

            self.analyze_with_result(document_id, self.valid_contract_result())

            project = db.session.get(Project, project.id)
            self.assertEqual(project.name, "Riverside Office Building")
            self.assertEqual(project.contract_value, 4850000)
            self.assertEqual(project.start_date.isoformat(), "2026-08-01")
            self.assertEqual(project.end_date.isoformat(), "2027-06-30")
            self.assertEqual(project.required_coverage, 2000000)

            document = db.session.get(Document, document_id)
            fields = document.ai_compliance_result["project_auto_fill"]["fields"]
            self.assertEqual(fields["project_name"]["status"], "applied")
            self.assertEqual(fields["contract_value"]["status"], "applied")

    def test_manual_values_are_not_overwritten(self):
        with self.app.app_context():
            project = self.make_project(
                name="Manual Project",
                contract_value=100,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31),
                required_coverage=1000000,
            )
            document_id = self.make_project_document(project)

            self.analyze_with_result(document_id, self.valid_contract_result())

            project = db.session.get(Project, project.id)
            self.assertEqual(project.name, "Manual Project")
            self.assertEqual(project.contract_value, 100)
            self.assertEqual(project.start_date.isoformat(), "2026-01-01")
            self.assertEqual(project.end_date.isoformat(), "2026-12-31")
            self.assertEqual(project.required_coverage, 1000000)

            document = db.session.get(Document, document_id)
            statuses = {
                field: value["status"]
                for field, value in document.ai_compliance_result[
                    "project_auto_fill"
                ]["fields"].items()
            }
            self.assertEqual(set(statuses.values()), {"preserved"})
            self.assertEqual(
                document.ai_extracted_data["contract_value"],
                4850000,
            )

    def test_low_confidence_and_validator_failure_do_not_fill(self):
        cases = [
            self.valid_contract_result(confidence=0.4),
            self.valid_contract_result(
                compliance_status="Blocked",
                issues=[
                    {
                        "field": "confidence",
                        "message": "Contract extraction confidence is too low.",
                    }
                ],
            ),
        ]

        for index, result in enumerate(cases):
            with self.subTest(index=index):
                with self.app.app_context():
                    project = self.make_project(name=f"Project {index}")
                    document_id = self.make_project_document(project)

                    self.analyze_with_result(document_id, result)

                    project = db.session.get(Project, project.id)
                    self.assertEqual(project.name, f"Project {index}")
                    self.assertEqual(project.contract_value, 0)
                    self.assertIsNone(project.start_date)
                    self.assertIsNone(project.end_date)
                    self.assertIsNone(project.required_coverage)

    def test_low_field_confidence_prevents_only_that_field(self):
        with self.app.app_context():
            project = self.make_project()
            document_id = self.make_project_document(project)

            self.analyze_with_result(
                document_id,
                self.valid_contract_result(
                    field_confidence={"contract_value": 0.2}
                ),
            )

            project = db.session.get(Project, project.id)
            self.assertEqual(project.name, "Riverside Office Building")
            self.assertEqual(project.contract_value, 0)
            self.assertEqual(project.required_coverage, 2000000)

            document = db.session.get(Document, document_id)
            self.assertEqual(
                document.ai_compliance_result["project_auto_fill"]["fields"][
                    "contract_value"
                ]["status"],
                "low_confidence",
            )

    def test_invalid_date_and_reversed_dates_do_not_fill_dates(self):
        cases = [
            {"start_date": "not-a-date", "end_date": "2027-06-30"},
            {"start_date": "2027-06-30", "end_date": "2026-08-01"},
        ]

        for index, data in enumerate(cases):
            with self.subTest(index=index):
                with self.app.app_context():
                    project = self.make_project()
                    document_id = self.make_project_document(project)

                    self.analyze_with_result(
                        document_id,
                        self.valid_contract_result(data=data),
                    )

                    project = db.session.get(Project, project.id)
                    self.assertIsNone(project.start_date)
                    self.assertIsNone(project.end_date)
                    self.assertEqual(project.required_coverage, 2000000)

                    document = db.session.get(Document, document_id)
                    fields = document.ai_compliance_result[
                        "project_auto_fill"
                    ]["fields"]
                    self.assertEqual(fields["start_date"]["status"], "invalid")
                    self.assertEqual(fields["end_date"]["status"], "invalid")

    def test_money_dates_and_coverage_are_normalized(self):
        normalized = normalize_contract_extraction(
            {
                "document_type": "contract",
                "project_name": "  Riverside Office Building  ",
                "contract_value": "USD 4,850,000",
                "start_date": "08/01/2026",
                "end_date": "06/30/2027",
                "required_coverage": "2M",
                "confidence": 0.95,
                "field_confidence": {},
            }
        )

        self.assertEqual(normalized["project_name"], "Riverside Office Building")
        self.assertEqual(normalized["contract_value"], 4850000)
        self.assertEqual(normalized["start_date"], "2026-08-01")
        self.assertEqual(normalized["end_date"], "2027-06-30")
        self.assertEqual(normalized["required_coverage"], 2000000)

        self.assertEqual(
            normalize_contract_extraction(
                {"contract_value": "4.85 million"}
            )["contract_value"],
            4850000,
        )
        self.assertEqual(
            normalize_contract_extraction(
                {"required_coverage": "$2,000,000 per occurrence"}
            )["required_coverage"],
            2000000,
        )
        self.assertIsNone(
            normalize_contract_extraction(
                {"contract_value": "0"}
            )["contract_value"]
        )
        self.assertIsNone(
            normalize_contract_extraction(
                {"contract_value": "-100"}
            )["contract_value"]
        )
        self.assertIsNone(
            normalize_contract_extraction(
                {"required_coverage": "not a limit"}
            )["required_coverage"]
        )

    def test_incomplete_and_invalid_json_fields_do_not_block_valid_fields(self):
        with self.app.app_context():
            project = self.make_project()
            document_id = self.make_project_document(project)

            self.analyze_with_result(
                document_id,
                self.valid_contract_result(
                    data={
                        "project_name": "",
                        "contract_value": "retainage only",
                        "start_date": "2026-08-01",
                        "end_date": "2027-06-30",
                        "required_coverage": None,
                    }
                ),
            )

            project = db.session.get(Project, project.id)
            self.assertEqual(project.name, "")
            self.assertEqual(project.contract_value, 0)
            self.assertEqual(project.start_date.isoformat(), "2026-08-01")
            self.assertEqual(project.end_date.isoformat(), "2027-06-30")
            self.assertIsNone(project.required_coverage)

            document = db.session.get(Document, document_id)
            fields = document.ai_compliance_result["project_auto_fill"]["fields"]
            self.assertEqual(fields["project_name"]["status"], "invalid")
            self.assertEqual(fields["contract_value"]["status"], "invalid")
            self.assertEqual(fields["required_coverage"]["status"], "invalid")

    def test_scope_owner_requirements_and_sub_documents_do_not_alter_project(self):
        with self.app.app_context():
            project = self.make_project()
            scope_document_id = self.make_project_document(project, "Scope")
            owner_document_id = self.make_project_document(
                project,
                "Owner Requirements",
            )
            subcontractor = Subcontractor(
                name="Sub",
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            db.session.add(subcontractor)
            db.session.flush()
            sub_document = Document(
                filename="subcontractors/1/contract.pdf",
                original_name="contract.pdf",
                document_type="Contract",
                sub_id=subcontractor.id,
                uploaded_by=self.user_id,
            )
            db.session.add(sub_document)
            db.session.commit()
            sub_document_id = sub_document.id

            for document_id in (
                scope_document_id,
                owner_document_id,
                sub_document_id,
            ):
                self.analyze_with_result(document_id, self.valid_contract_result())

            project = db.session.get(Project, project.id)
            self.assertEqual(project.name, "")
            self.assertEqual(project.contract_value, 0)
            self.assertIsNone(project.required_coverage)

    def test_unknown_type_and_coi_do_not_alter_project(self):
        with self.app.app_context():
            project = self.make_project()
            unknown_document_id = self.make_project_document(project, "Other")
            coi_document_id = self.make_project_document(project, "COI")

            self.analyze_with_result(
                unknown_document_id,
                self.valid_contract_result(),
            )
            self.analyze_with_result(
                coi_document_id,
                self.valid_contract_result(),
            )

            project = db.session.get(Project, project.id)
            self.assertEqual(project.name, "")
            self.assertEqual(project.contract_value, 0)
            self.assertIsNone(project.required_coverage)

        with tempfile.NamedTemporaryFile(suffix=".pdf") as temp_file, patch.dict(
            os.environ,
            {"AI_MOCK_MODE": "true"},
        ):
            result = analyze_document_intelligence(
                file_path=temp_file.name,
                document_type="Mystery",
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["category"], "other")

    def test_unauthorized_project_document_route_does_not_alter_other_tenant(self):
        with self.app.app_context():
            other_project = self.make_project(
                name="Other Project",
                user_id=self.other_user_id,
                organization_id=self.other_organization_id,
            )
            document_id = self.make_project_document(other_project)
            project_id = other_project.id

        self.login(self.user_id, self.organization_id)

        with patch(
            "app.routes.ai.analyze_and_save_document"
        ) as analyze_document:
            response = self.client.post(f"/documents/{document_id}/analyze")

        self.assertEqual(response.status_code, 404)
        analyze_document.assert_not_called()

        with self.app.app_context():
            project = db.session.get(Project, project_id)
            self.assertEqual(project.name, "Other Project")
            self.assertEqual(project.contract_value, 0)

    def test_analysis_rollback_prevents_partial_project_update(self):
        with self.app.app_context():
            project = self.make_project()
            document_id = self.make_project_document(project)
            project_id = project.id

        self.login(self.user_id, self.organization_id)

        with patch(
            "app.services.document_analysis_service.temporary_document_path",
            new=temporary_analysis_path,
        ), patch(
            "app.services.document_analysis_service.analyze_document_intelligence",
            return_value=self.valid_contract_result(),
        ), patch(
            "app.services.document_analysis_service.apply_contract_extraction_to_project",
            side_effect=RuntimeError("boom"),
        ):
            response = self.client.post(f"/documents/{document_id}/analyze")

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            project = db.session.get(Project, project_id)
            document = db.session.get(Document, document_id)
            self.assertEqual(project.name, "")
            self.assertEqual(project.contract_value, 0)
            self.assertEqual(document.ai_status, "failed")

    def test_reanalysis_is_idempotent_and_preserves_manual_updates(self):
        with self.app.app_context():
            project = self.make_project()
            document_id = self.make_project_document(project)

            self.analyze_with_result(document_id, self.valid_contract_result())
            project = db.session.get(Project, project.id)
            self.assertEqual(project.contract_value, 4850000)

            project.contract_value = 999
            db.session.commit()

            self.analyze_with_result(document_id, self.valid_contract_result())

            project = db.session.get(Project, project.id)
            self.assertEqual(project.contract_value, 999)
            document = db.session.get(Document, document_id)
            self.assertEqual(
                document.ai_compliance_result["project_auto_fill"]["fields"][
                    "contract_value"
                ]["status"],
                "preserved",
            )

    def test_contract_mock_mode_is_supported(self):
        temp_file = tempfile.NamedTemporaryFile(
            suffix=".pdf",
            mode="w",
            encoding="utf-8",
            delete=False,
        )

        try:
            temp_file.write(
                "\n".join(
                    [
                        "Project Name: Mock Contract Project",
                        "Contract Sum: $3,000,000",
                        "Start Date: 2026-08-01",
                        "Completion Date: 2027-06-30",
                        "Minimum General Liability: $2,000,000 per occurrence",
                    ]
                )
            )
            temp_file.close()

            with patch.dict(
                os.environ,
                {"AI_MOCK_MODE": "true"},
            ):
                result = analyze_document_intelligence(
                    file_path=temp_file.name,
                    document_type="Contract",
                )

        finally:
            try:
                os.remove(temp_file.name)
            except OSError:
                pass

        self.assertTrue(result["success"])
        self.assertEqual(result["category"], "contract")
        self.assertEqual(
            result["extracted_data"]["project_name"],
            "Mock Contract Project",
        )
        self.assertEqual(result["extracted_data"]["contract_value"], 3000000)
        self.assertEqual(result["extracted_data"]["required_coverage"], 2000000)
        self.assertEqual(result["compliance"]["status"], "Ready")

    def test_add_project_without_document_does_not_auto_analyze(self):
        self.login()

        with patch(
            "app.routes.projects.analyze_and_save_document"
        ) as analyze_document:
            response = self.client.post(
                "/add_project",
                data={
                    "name": "No Document Project",
                    "contract_value": "0",
                    "required_coverage_choice": "",
                },
            )

        self.assertEqual(response.status_code, 302)
        analyze_document.assert_not_called()

    def test_add_project_contract_auto_analyzes_once_and_fills_empty_fields(self):
        self.login()

        with patch(
            "app.routes.projects.analyze_and_save_document",
            wraps=analyze_and_save_document,
        ) as analyze_document, patch(
            "app.services.document_analysis_service.analyze_document_intelligence",
            return_value=self.valid_contract_result(),
        ):
            response = self.client.post(
                "/add_project",
                data={
                    "name": "Project",
                    "contract_value": "0",
                    "required_coverage_choice": "",
                    "doc_type": "Contract",
                    "documents": (
                        BytesIO(b"%PDF-1.4\ncontract"),
                        "contract.pdf",
                    ),
                },
                content_type="multipart/form-data",
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(analyze_document.call_count, 1)
        body = response.get_data(as_text=True)
        self.assertIn(
            "Project created and contract analyzed successfully.",
            body,
        )

        with self.app.app_context():
            project = Project.query.filter_by(
                name="Riverside Office Building"
            ).one()
            document = Document.query.filter_by(
                project_id=project.id,
                document_type="Contract",
            ).one()

            self.assertEqual(project.contract_value, 4850000)
            self.assertEqual(project.start_date.isoformat(), "2026-08-01")
            self.assertEqual(project.end_date.isoformat(), "2027-06-30")
            self.assertEqual(project.required_coverage, 2000000)
            self.assertEqual(document.ai_status, "analyzed")

    def test_add_project_contract_auto_analysis_preserves_visible_manual_fields(self):
        self.login()

        with patch(
            "app.routes.projects.analyze_and_save_document",
            wraps=analyze_and_save_document,
        ) as analyze_document, patch(
            "app.services.document_analysis_service.analyze_document_intelligence",
            return_value=self.valid_contract_result(),
        ):
            response = self.client.post(
                "/add_project",
                data={
                    "name": "Manual Project",
                    "contract_value": "100",
                    "start_date": "2026-01-01",
                    "end_date": "2026-12-31",
                    "required_coverage_choice": "1000000",
                    "doc_type": "Contract",
                    "documents": (
                        BytesIO(b"%PDF-1.4\ncontract"),
                        "contract.pdf",
                    ),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(analyze_document.call_count, 1)

        with self.app.app_context():
            project = Project.query.filter_by(name="Manual Project").one()
            document = Document.query.filter_by(project_id=project.id).one()

            self.assertEqual(project.contract_value, 4850000)
            self.assertEqual(project.start_date.isoformat(), "2026-08-01")
            self.assertEqual(project.end_date.isoformat(), "2026-12-31")
            self.assertEqual(project.required_coverage, 1000000)
            fields = document.ai_compliance_result["project_auto_fill"]["fields"]
            self.assertEqual(fields["contract_value"]["status"], "applied")
            self.assertEqual(fields["start_date"]["status"], "applied")
            self.assertEqual(fields["end_date"]["status"], "preserved")
            self.assertEqual(fields["required_coverage"]["status"], "preserved")

    def test_add_project_scope_and_owner_requirements_do_not_auto_analyze(self):
        self.login()

        for doc_type in ("Scope", "Owner Requirements"):
            with self.subTest(doc_type=doc_type), patch(
                "app.routes.projects.analyze_and_save_document"
            ) as analyze_document:
                response = self.client.post(
                    "/add_project",
                    data={
                        "name": f"{doc_type} Project",
                        "contract_value": "0",
                        "required_coverage_choice": "",
                        "doc_type": doc_type,
                        "documents": (
                            BytesIO(b"%PDF-1.4\nproject document"),
                            f"{doc_type}.pdf",
                        ),
                    },
                    content_type="multipart/form-data",
                )

                self.assertEqual(response.status_code, 302)
                analyze_document.assert_not_called()

    def test_add_project_contract_analysis_failure_preserves_project_document_and_file(self):
        self.login()

        with patch(
            "app.routes.projects.analyze_and_save_document",
            side_effect=RuntimeError("analysis unavailable"),
        ) as analyze_document:
            response = self.client.post(
                "/add_project",
                data={
                    "name": "Analysis Failure Project",
                    "contract_value": "0",
                    "required_coverage_choice": "",
                    "doc_type": "Contract",
                    "documents": (
                        BytesIO(b"%PDF-1.4\ncontract"),
                        "contract.pdf",
                    ),
                },
                content_type="multipart/form-data",
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(analyze_document.call_count, 1)
        self.assertIn(
            "Project created, but automatic contract analysis could not be completed. "
            "You can retry from the project page.",
            response.get_data(as_text=True),
        )

        with self.app.app_context():
            project = Project.query.filter_by(
                name="Analysis Failure Project"
            ).one()
            document = Document.query.filter_by(
                project_id=project.id,
                document_type="Contract",
            ).one()

            self.assertEqual(document.ai_status, "failed")
            self.assertTrue(
                os.path.exists(
                    os.path.join(self.uploads.name, document.filename)
                )
            )
            document_id = document.id

        with patch(
            "app.routes.ai.analyze_and_save_document",
            return_value={
                "success": True,
                "error": None,
                "document": Document(
                    id=document_id,
                    project_id=project.id,
                ),
                "result": {},
            },
        ) as retry_analysis:
            retry = self.client.post(f"/documents/{document_id}/analyze")

        self.assertEqual(retry.status_code, 302)
        retry_analysis.assert_called_once_with(document_id)

    def test_project_view_displays_contract_extraction_block_safely(self):
        with self.app.app_context():
            project = self.make_project(name="Project")
            document = Document(
                filename="projects/1/contract.pdf",
                original_name="contract.pdf",
                document_type="Contract",
                project_id=project.id,
                uploaded_by=self.user_id,
                ai_status="analyzed",
                ai_confidence=0.95,
                ai_extracted_data=normalize_contract_extraction(
                    {
                        "project_name": "<script>Bad</script>",
                        "contract_value": "$4,850,000",
                        "start_date": "08/01/2026",
                        "end_date": "06/30/2027",
                        "required_coverage": "2M",
                        "confidence": 0.95,
                        "field_confidence": {
                            "project_name": 0.94,
                            "contract_value": 0.97,
                            "start_date": 0.93,
                            "end_date": 0.91,
                            "required_coverage": 0.90,
                        },
                    }
                ),
                ai_compliance_result={
                    "status": "Ready",
                    "project_auto_fill": {
                        "eligible": True,
                        "fields": {
                            "project_name": {"status": "applied"},
                            "contract_value": {"status": "applied"},
                            "start_date": {"status": "applied"},
                            "end_date": {"status": "applied"},
                            "required_coverage": {"status": "applied"},
                        },
                    },
                },
            )
            db.session.add(document)
            db.session.commit()
            project_id = project.id

        self.login(self.user_id, self.organization_id)
        response = self.client.get(f"/project/{project_id}")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Contract Extraction", body)
        self.assertIn("Applied to project", body)
        self.assertIn("$4,850,000", body)
        self.assertIn("&lt;script&gt;Bad&lt;/script&gt;", body)
        self.assertNotIn("<script>Bad</script>", body)

    def test_project_view_shows_invalid_status_for_contract_fields(self):
        with self.app.app_context():
            project = self.make_project(name="Project")
            document = Document(
                filename="projects/1/contract.pdf",
                original_name="contract.pdf",
                document_type="Contract",
                project_id=project.id,
                uploaded_by=self.user_id,
                ai_status="analyzed",
                ai_extracted_data=normalize_contract_extraction(
                    {
                        "document_type": "contract",
                        "project_name": "",
                        "contract_value": "0",
                        "confidence": 0.95,
                        "field_confidence": {
                            "project_name": 0.94,
                            "contract_value": 0.97,
                        },
                    }
                ),
                ai_compliance_result={
                    "status": "Ready",
                    "project_auto_fill": {
                        "eligible": True,
                        "fields": {
                            "project_name": {"status": "invalid"},
                            "contract_value": {"status": "invalid"},
                        },
                    },
                },
            )
            db.session.add(document)
            db.session.commit()
            project_id = project.id

        self.login(self.user_id, self.organization_id)
        response = self.client.get(f"/project/{project_id}")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Invalid", body)

    def test_coi_auto_fill_and_readiness_still_work(self):
        with self.app.app_context():
            project = self.make_project(
                name="Coverage Project",
                required_coverage=2000000,
            )
            subcontractor = Subcontractor(
                name="COI Sub",
                user_id=self.user_id,
                organization_id=self.organization_id,
                coi_expiration=None,
            )
            db.session.add(subcontractor)
            db.session.flush()
            link = ProjectSubcontractor(
                project_id=project.id,
                subcontractor_id=subcontractor.id,
                coverage_limit=1000000,
            )
            document = Document(
                filename="subcontractors/1/coi.pdf",
                original_name="coi.pdf",
                document_type="COI",
                sub_id=subcontractor.id,
                uploaded_by=self.user_id,
            )
            db.session.add_all([link, document])
            db.session.commit()
            document_id = document.id
            subcontractor_id = subcontractor.id
            link_id = link.id

            self.analyze_with_result(
                document_id,
                {
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
            )

            subcontractor = db.session.get(Subcontractor, subcontractor_id)
            self.assertIsNotNone(subcontractor.coi_expiration)
            readiness = calculate_readiness(db.session.get(ProjectSubcontractor, link_id))
            self.assertEqual(readiness["status"], BLOCKED)
            self.assertIn(
                "COVERAGE_INSUFFICIENT",
                {reason["code"] for reason in readiness["reasons"]},
            )

            document = db.session.get(Document, document_id)
            document.ai_extracted_data["general_liability_limit"] = 2000000
            flag_modified(document, "ai_extracted_data")
            db.session.get(ProjectSubcontractor, link_id).coverage_limit = 2000000
            db.session.flush()
            self.assertEqual(
                calculate_readiness(
                    db.session.get(ProjectSubcontractor, link_id)
                )["status"],
                READY,
            )


if __name__ == "__main__":
    unittest.main()
