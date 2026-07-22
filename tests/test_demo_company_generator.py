import json
import os
import tempfile
import unittest

from pathlib import Path


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Document, Project, ProjectSubcontractor, Subcontractor, User
from app.services.demo_company_generator.generator import (
    generate_demo_company_package,
)
from app.services.document_intelligence.engine import _decode_document_text
from app.services.organizations import create_default_organization_for_user


EXPECTED_FILES = {
    "Certificate_of_Insurance.pdf",
    "W9_Demo.pdf",
    "Safety_Manual.pdf",
    "EMR_Verification_Letter.pdf",
    "OSHA_Compliance_Letter.pdf",
    "Contractor_License.pdf",
    "Vendor_Information_Form.pdf",
    "Company_Profile.pdf",
    "Scope_of_Work.pdf",
    "Subcontract_Agreement.pdf",
}


class DemoCompanyGeneratorTest(unittest.TestCase):

    def setUp(self):
        self.output = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.output.cleanup()

    def generate(self, **overrides):
        options = {
            "preset": "apex-concrete",
            "scenario": "ready",
            "output": self.output.name,
            "seed": 123,
            "create_zip": False,
            "create_records": False,
        }
        options.update(overrides)
        return generate_demo_company_package(**options)

    def text_for(self, path):
        return _decode_document_text(Path(path).read_bytes())

    def compact_text_for(self, path):
        return " ".join(self.text_for(path).split())

    def test_generates_complete_apex_package(self):
        result = self.generate(create_zip=True)
        output_dir = result["output_dir"]

        self.assertEqual(output_dir.name, "apex_concrete_llc")
        self.assertTrue((output_dir / "company.json").exists())
        self.assertTrue((output_dir / "manifest.json").exists())
        self.assertTrue(result["zip_path"].exists())
        self.assertGreater(result["zip_path"].stat().st_size, 1000)

        filenames = {
            document.filename
            for document in result["documents"]
        }
        self.assertEqual(filenames, EXPECTED_FILES)

        for document in result["documents"]:
            with self.subTest(filename=document.filename):
                self.assertTrue(document.path.exists())
                self.assertGreater(document.path.stat().st_size, 1000)
                self.assertGreaterEqual(document.page_count, 1)
                self.assertEqual(len(document.sha256), 64)
                text = self.text_for(document.path)
                self.assertIn(
                    "DEMO DOCUMENT - NOT VALID FOR COMMERCIAL OR LEGAL USE",
                    text,
                )
                self.assertIn("Apex Concrete", text)

    def test_manifest_and_company_json_are_consistent(self):
        result = self.generate()
        company_data = json.loads(result["company_json"].read_text())
        manifest = json.loads(result["manifest_path"].read_text())

        self.assertTrue(company_data["fictitious"])
        self.assertEqual(
            company_data["company"]["legal_name"],
            "Apex Concrete LLC",
        )
        self.assertEqual(
            company_data["company"]["trade"],
            "Concrete Contractor",
        )
        self.assertEqual(company_data["company"]["city"], "Dallas")
        self.assertEqual(company_data["company"]["state"], "TX")
        self.assertEqual(company_data["company"]["ein"][-4:], "1857")
        self.assertEqual(manifest["scenario"], "ready")
        self.assertEqual(len(manifest["documents"]), 10)
        self.assertEqual(
            {
                document["filename"]
                for document in manifest["documents"]
            },
            EXPECTED_FILES,
        )

    def test_seed_reproduces_company_identity(self):
        first = self.generate(seed=123)
        second = self.generate(seed=123)

        first_company = json.loads(first["company_json"].read_text())
        second_company = json.loads(second["company_json"].read_text())

        self.assertEqual(
            first_company["company"]["ein"],
            second_company["company"]["ein"],
        )
        self.assertEqual(
            first_company["company"]["license_number"],
            second_company["company"]["license_number"],
        )

    def test_ready_pending_and_blocked_scenarios_control_coi_data(self):
        ready = self.generate(scenario="ready")
        pending = self.generate(scenario="pending")
        blocked = self.generate(scenario="blocked")

        self.assertEqual(
            ready["scenario"].policy.general_liability_each_occurrence,
            2_000_000,
        )
        self.assertGreater(
            (
                pending["scenario"].policy.expiration_date
                - pending["scenario"].policy.effective_date
            ).days,
            0,
        )
        self.assertLessEqual(
            (
                pending["scenario"].policy.expiration_date
                - __import__("datetime").date.today()
            ).days,
            30,
        )
        self.assertLess(
            blocked["scenario"].policy.general_liability_each_occurrence,
            blocked["scenario"].required_coverage,
        )

    def test_coi_contains_required_limits_and_labels(self):
        result = self.generate()
        coi = next(
            document
            for document in result["documents"]
            if document.filename == "Certificate_of_Insurance.pdf"
        )
        text = self.compact_text_for(coi.path)

        for value in (
            "Certificate of Insurance",
            "Producer",
            "Insured",
            "Commercial General Liability",
            "$2,000,000",
            "$4,000,000",
            "$1,000,000",
            "$5,000,000",
            "Additional Insured",
            "Waiver of Subrogation",
            "Summit Distribution Center",
        ):
            self.assertIn(value, text)

    def test_safety_manual_has_at_least_twelve_pages(self):
        result = self.generate()
        safety = next(
            document
            for document in result["documents"]
            if document.filename == "Safety_Manual.pdf"
        )
        self.assertGreaterEqual(safety.page_count, 12)

    def test_documents_are_demo_only_and_use_fictitious_data(self):
        result = self.generate()

        for document in result["documents"]:
            text = self.text_for(document.path)
            self.assertIn("DEMO DOCUMENT", text)
            self.assertIn("demo", text.lower())
            self.assertNotIn("Internal Revenue Service", text)
            self.assertNotIn("OSHA seal", text)

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
                organization = create_default_organization_for_user(user)
                project = Project(
                    name="Summit Distribution Center",
                    user_id=user.id,
                    organization_id=organization.id,
                    required_coverage=2_000_000,
                )
                db.session.add(project)
                db.session.commit()

                self.generate(create_records=True)
                self.generate(create_records=True)

                self.assertEqual(
                    Subcontractor.query.filter_by(
                        name="Apex Concrete LLC",
                    ).count(),
                    1,
                )
                subcontractor = Subcontractor.query.filter_by(
                    name="Apex Concrete LLC",
                ).one()
                self.assertEqual(
                    Document.query.filter_by(
                        sub_id=subcontractor.id,
                    ).count(),
                    10,
                )
                self.assertEqual(
                    ProjectSubcontractor.query.filter_by(
                        project_id=project.id,
                        subcontractor_id=subcontractor.id,
                    ).count(),
                    1,
                )

                db.session.remove()
                db.drop_all()
                db.engine.dispose()


if __name__ == "__main__":
    unittest.main()
