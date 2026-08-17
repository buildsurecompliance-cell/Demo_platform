import json
import os
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Document, Project, ProjectSubcontractor, Subcontractor, User
from app.services.demo_project_generator.project_factory import list_demo_projects
from app.services.demo_project_generator.project_generator import (
    generate_demo_environment,
    generate_demo_project_package,
)
from app.services.document_intelligence.engine import (
    _decode_document_text,
    analyze_document_intelligence,
)
from app.services.organizations import create_default_organization_for_user
from app.services.readiness_service import BLOCKED, PENDING, READY, calculate_readiness


PROJECT_DOCUMENTS = {
    "Prime_Contract.pdf",
    "Owner_Requirements.pdf",
    "Project_Scope.pdf",
    "General_Conditions.pdf",
    "Insurance_Requirements.pdf",
    "Site_Logistics_Plan.pdf",
    "Project_Schedule.pdf",
    "Project_Directory.pdf",
    "Mobilization_Checklist.pdf",
    "Safety_Requirements.pdf",
}


class DemoProjectGeneratorTest(unittest.TestCase):

    def setUp(self):
        self.output = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.output.cleanup()

    def test_all_project_presets_generate_required_documents(self):
        projects = list_demo_projects(seed=123)
        self.assertEqual(len(projects), 5)

        for project in projects:
            with self.subTest(project=project.name):
                result = generate_demo_project_package(
                    preset=project.key,
                    output=self.output.name,
                    seed=123,
                )
                filenames = {
                    document.filename
                    for document in result["documents"]
                }
                self.assertEqual(filenames, PROJECT_DOCUMENTS)
                self.assertIn("Prime_Contract.pdf", filenames)
                self.assertIn("Owner_Requirements.pdf", filenames)
                self.assertIn("Project_Scope.pdf", filenames)

    def test_all_prime_contracts_are_analyzable_in_mock_mode(self):
        with patch.dict(os.environ, {"AI_MOCK_MODE": "true"}):
            for project in list_demo_projects(seed=123):
                with self.subTest(project=project.name):
                    result = generate_demo_project_package(
                        preset=project.key,
                        output=self.output.name,
                        seed=123,
                    )
                    contract = next(
                        document
                        for document in result["documents"]
                        if document.document_type == "Contract"
                    )
                    analysis = analyze_document_intelligence(
                        file_path=str(contract.path),
                        document_type="Contract",
                    )

                    self.assertTrue(analysis["success"])
                    self.assertEqual(
                        analysis["extracted_data"]["project_name"],
                        project.name,
                    )
                    self.assertEqual(
                        analysis["extracted_data"]["contract_value"],
                        project.contract_value,
                    )
                    self.assertEqual(
                        analysis["extracted_data"]["required_coverage"],
                        project.required_insurance_limit,
                    )

    def test_populated_project_generates_analyzable_cois(self):
        result = generate_demo_project_package(
            preset="summit-distribution-center",
            output=self.output.name,
            seed=123,
            populate=True,
        )
        self.assertEqual(len(result["subcontractors"]), 10)

        with patch.dict(os.environ, {"AI_MOCK_MODE": "true"}):
            for subcontractor in result["subcontractors"]:
                coi = next(
                    document
                    for document in subcontractor["documents"]
                    if document.document_type == "COI"
                )
                analysis = analyze_document_intelligence(
                    file_path=str(coi.path),
                    document_type="COI",
                )
                self.assertTrue(analysis["success"])
                self.assertIn("expiration_date", analysis["extracted_data"])

    def test_environment_generates_five_projects_and_fifty_subcontractors(self):
        result = generate_demo_environment(
            output=self.output.name,
            seed=123,
            scenario="mixed",
        )

        self.assertEqual(len(result["projects"]), 5)
        subcontractor_count = sum(
            len(project["subcontractors"])
            for project in result["projects"]
        )
        project_document_count = sum(
            len(project["documents"])
            for project in result["projects"]
        )
        subcontractor_document_count = sum(
            len(subcontractor["documents"])
            for project in result["projects"]
            for subcontractor in project["subcontractors"]
        )

        self.assertEqual(subcontractor_count, 50)
        self.assertEqual(project_document_count, 50)
        self.assertEqual(subcontractor_document_count, 500)
        self.assertEqual(
            result["statistics"],
            {"READY": 30, "PENDING": 10, "BLOCKED": 10},
        )
        self.assertTrue(result["manifest_path"].exists())

    def test_environment_is_reproducible_with_same_seed(self):
        first = generate_demo_environment(
            output=self.output.name,
            seed=123,
            scenario="mixed",
        )
        first_manifest = json.loads(first["manifest_path"].read_text())

        second = generate_demo_environment(
            output=self.output.name,
            seed=123,
            scenario="mixed",
        )
        second_manifest = json.loads(second["manifest_path"].read_text())

        self.assertEqual(
            _stable_manifest(first_manifest),
            _stable_manifest(second_manifest),
        )

    def test_scenarios_produce_expected_readiness(self):
        app = create_app(TestingConfig)
        app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            RATELIMIT_ENABLED=False,
        )

        with app.app_context():
            db.drop_all()
            db.create_all()
            user = User(email="owner@example.com", paid=True)
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()
            organization = create_default_organization_for_user(user)
            project = Project(
                name="Summit Distribution Center",
                user_id=user.id,
                organization_id=organization.id,
                required_coverage=2_000_000,
            )
            db.session.add(project)
            db.session.flush()

            statuses = {}
            for scenario_key in ("ready", "pending", "blocked"):
                package = generate_demo_project_package(
                    preset="summit-distribution-center",
                    output=self.output.name,
                    seed=123,
                    populate=True,
                )
                sub_package = next(
                    item
                    for item in package["subcontractors"]
                    if item["scenario"].key == scenario_key
                )
                company = sub_package["company"]
                scenario = sub_package["scenario"]
                sub = Subcontractor(
                    name=f"{company.legal_name} {scenario_key}",
                    email=company.email,
                    phone=company.phone,
                    role=company.trade,
                    coi_expiration=scenario.policy.expiration_date,
                    user_id=user.id,
                    organization_id=organization.id,
                )
                db.session.add(sub)
                db.session.flush()
                link = ProjectSubcontractor(
                    project_id=project.id,
                    subcontractor_id=sub.id,
                    coverage_limit=scenario.policy.general_liability_each_occurrence,
                )
                db.session.add(link)
                db.session.add(
                    Document(
                        filename=f"{scenario_key}_coi.pdf",
                        original_name=f"{scenario_key}_coi.pdf",
                        document_type="COI",
                        sub_id=sub.id,
                        uploaded_by=user.id,
                        ai_status="analyzed",
                        ai_confidence=0.95,
                        ai_extracted_data={
                            "expiration_date": (
                                scenario.policy.expiration_date.isoformat()
                            ),
                            "general_liability_each_occurrence": (
                                scenario.policy.general_liability_each_occurrence
                            ),
                            "general_liability_limit": (
                                scenario.policy.general_liability_each_occurrence
                            ),
                            "confidence": 0.95,
                        },
                        ai_compliance_result={
                            "status": "Ready",
                            "issues": [],
                            "warnings": [],
                            "confidence": 0.95,
                        },
                    )
                )
                db.session.flush()
                statuses[scenario_key] = calculate_readiness(link)["status"]

            self.assertEqual(statuses["ready"], READY)
            self.assertEqual(statuses["pending"], PENDING)
            self.assertEqual(statuses["blocked"], BLOCKED)

            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def test_create_records_is_idempotent(self):
        with tempfile.TemporaryDirectory() as uploads:
            app = create_app(TestingConfig)
            app.config.update(
                TESTING=True,
                WTF_CSRF_ENABLED=False,
                UPLOAD_FOLDER=uploads,
                STORAGE_BACKEND="local",
                RATELIMIT_ENABLED=False,
            )

            with app.app_context():
                db.drop_all()
                db.create_all()
                user = User(email="owner@example.com", paid=True)
                user.set_password("password123")
                db.session.add(user)
                db.session.flush()
                create_default_organization_for_user(user)
                db.session.commit()

                generate_demo_project_package(
                    preset="summit-distribution-center",
                    output=self.output.name,
                    seed=123,
                    populate=True,
                    create_records=True,
                )
                generate_demo_project_package(
                    preset="summit-distribution-center",
                    output=self.output.name,
                    seed=123,
                    populate=True,
                    create_records=True,
                )

                self.assertEqual(Project.query.count(), 1)
                self.assertEqual(Subcontractor.query.count(), 10)
                self.assertEqual(ProjectSubcontractor.query.count(), 10)
                self.assertEqual(Document.query.count(), 110)

                db.session.remove()
                db.drop_all()
                db.engine.dispose()


def _stable_manifest(manifest):
    for project in manifest["projects"]:
        for document in project["documents"]:
            document.pop("sha256", None)
        for subcontractor in project["subcontractors"]:
            for document in subcontractor["documents"]:
                document.pop("sha256", None)
    return manifest


if __name__ == "__main__":
    unittest.main()
