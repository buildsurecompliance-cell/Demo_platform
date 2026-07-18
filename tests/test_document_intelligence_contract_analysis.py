import os
import tempfile
import unittest

from contextlib import contextmanager
from unittest.mock import patch


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Document, Project, User
from app.services.document_analysis_service import analyze_and_save_document
from app.services.document_intelligence.engine import analyze_document_intelligence
from app.services.organizations import create_default_organization_for_user


RIVERSIDE_CONTRACT = """
Project Name: Riverside Office Building
Contract Sum: $4,850,000
Start Date: 2026-08-01
Completion Date: 2027-06-30
Minimum General Liability: $2,000,000 per occurrence
"""

WESTSIDE_CONTRACT = """
Project Name: Westside Medical Center
Contract Value: $7,250,000
Commencement Date: 2026-09-15
Substantial Completion Date: 2027-12-31
Required General Liability: $5,000,000 per occurrence
"""

SUMMIT_CONTRACT = """
Project
Summit Distribution Center
Owner
Summit Logistics Holdings LLC
Contract Value
$14,500,000
Start Date
March 1, 2026
End Date
October 31, 2027
Commercial General Liability
$2,000,000
"""

OAKRIDGE_CONTRACT = """
Project Name: Oakridge Civic Plaza
Client: Oakridge Public Facilities Authority
Contract Amount: $21,750,000
Commencement Date: April 15, 2026
Substantial Completion: September 30, 2028
Commercial General Liability $5,000,000 aggregate. Umbrella Liability $10,000,000.
"""

HARBOR_POINT_CONTRACT = """
Project: Harbor Point Renovation
Owner: Harbor Point Holdings
The Contract Sum is $9,600,000.
The contractual start date is July 1, 2026.
Substantial Completion is required no later than May 31, 2027.
Commercial General Liability
$2,000,000
"""


def write_contract_file(content):
    temp_file = tempfile.NamedTemporaryFile(
        suffix=".pdf",
        mode="w",
        encoding="utf-8",
        delete=False,
    )
    temp_file.write(content)
    temp_file.close()
    return temp_file.name


@contextmanager
def temporary_path(path):
    yield path


class DocumentIntelligenceContractAnalysisTest(unittest.TestCase):

    def setUp(self):
        self.temp_paths = []

    def tearDown(self):
        for path in self.temp_paths:
            try:
                os.remove(path)
            except OSError:
                pass

    def make_contract_path(self, content):
        path = write_contract_file(content)
        self.temp_paths.append(path)
        return path

    def analyze_contract(self, content):
        path = self.make_contract_path(content)

        with patch.dict(os.environ, {"AI_MOCK_MODE": "true"}):
            return analyze_document_intelligence(
                file_path=path,
                document_type="Contract",
            )

    def test_contract_analysis_uses_current_file_after_riverside(self):
        first = self.analyze_contract(RIVERSIDE_CONTRACT)
        second = self.analyze_contract(WESTSIDE_CONTRACT)

        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        self.assertEqual(
            first["extracted_data"]["project_name"],
            "Riverside Office Building",
        )
        self.assertEqual(
            second["extracted_data"]["project_name"],
            "Westside Medical Center",
        )
        self.assertEqual(second["extracted_data"]["contract_value"], 7250000)
        self.assertEqual(second["extracted_data"]["required_coverage"], 5000000)
        self.assertNotEqual(
            first["document_hash"],
            second["document_hash"],
        )

    def test_contract_analysis_uses_current_file_after_westside(self):
        first = self.analyze_contract(WESTSIDE_CONTRACT)
        second = self.analyze_contract(RIVERSIDE_CONTRACT)

        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        self.assertEqual(
            first["extracted_data"]["project_name"],
            "Westside Medical Center",
        )
        self.assertEqual(
            second["extracted_data"]["project_name"],
            "Riverside Office Building",
        )
        self.assertEqual(second["extracted_data"]["contract_value"], 4850000)
        self.assertEqual(second["extracted_data"]["required_coverage"], 2000000)

    def test_same_document_type_changes_with_file_content(self):
        riverside = self.analyze_contract(RIVERSIDE_CONTRACT)
        westside = self.analyze_contract(WESTSIDE_CONTRACT)

        self.assertEqual(riverside["category"], "contract")
        self.assertEqual(westside["category"], "contract")
        self.assertNotEqual(
            riverside["extracted_data"]["project_name"],
            westside["extracted_data"]["project_name"],
        )
        self.assertNotEqual(
            riverside["extracted_data"]["contract_value"],
            westside["extracted_data"]["contract_value"],
        )

    def test_contract_parser_accepts_demo_label_variations(self):
        cases = [
            (
                SUMMIT_CONTRACT,
                "Summit Distribution Center",
                14500000,
                "2026-03-01",
                "2027-10-31",
                2000000,
            ),
            (
                WESTSIDE_CONTRACT,
                "Westside Medical Center",
                7250000,
                "2026-09-15",
                "2027-12-31",
                5000000,
            ),
            (
                RIVERSIDE_CONTRACT,
                "Riverside Office Building",
                4850000,
                "2026-08-01",
                "2027-06-30",
                2000000,
            ),
            (
                OAKRIDGE_CONTRACT,
                "Oakridge Civic Plaza",
                21750000,
                "2026-04-15",
                "2028-09-30",
                5000000,
            ),
            (
                HARBOR_POINT_CONTRACT,
                "Harbor Point Renovation",
                9600000,
                "2026-07-01",
                "2027-05-31",
                2000000,
            ),
        ]

        for (
            content,
            project_name,
            contract_value,
            start_date,
            end_date,
            required_coverage,
        ) in cases:
            with self.subTest(project_name=project_name):
                result = self.analyze_contract(content)

                self.assertTrue(result["success"])
                self.assertEqual(
                    result["extracted_data"]["project_name"],
                    project_name,
                )
                self.assertEqual(
                    result["extracted_data"]["contract_value"],
                    contract_value,
                )
                self.assertEqual(
                    result["extracted_data"]["start_date"],
                    start_date,
                )
                self.assertEqual(
                    result["extracted_data"]["end_date"],
                    end_date,
                )
                self.assertEqual(
                    result["extracted_data"]["required_coverage"],
                    required_coverage,
                )

    def test_invalid_or_empty_contract_file_fails_without_fixture_data(self):
        path = self.make_contract_path("\x00\x00\x00")

        with patch.dict(os.environ, {"AI_MOCK_MODE": "true"}):
            result = analyze_document_intelligence(
                file_path=path,
                document_type="Contract",
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["extracted_data"], {})
        self.assertEqual(result["compliance"], {})
        self.assertIn(
            "could not be extracted",
            result["error"],
        )


class DocumentAnalysisPersistenceTest(unittest.TestCase):

    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory()
        self.app = create_app(TestingConfig)
        self.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            UPLOAD_FOLDER=self.uploads.name,
            RATELIMIT_ENABLED=False,
        )
        self.temp_paths = []

        with self.app.app_context():
            db.drop_all()
            db.create_all()

            user = User(email="owner@example.com", paid=True)
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()
            organization = create_default_organization_for_user(user)
            project = Project(
                name="",
                user_id=user.id,
                organization_id=organization.id,
            )
            db.session.add(project)
            db.session.flush()
            document = Document(
                filename="projects/1/contract.pdf",
                original_name="contract.pdf",
                document_type="Contract",
                project_id=project.id,
                uploaded_by=user.id,
            )
            db.session.add(document)
            db.session.commit()

            self.document_id = document.id
            self.project_id = project.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

        for path in self.temp_paths:
            try:
                os.remove(path)
            except OSError:
                pass

        self.uploads.cleanup()

    def make_contract_path(self, content):
        path = write_contract_file(content)
        self.temp_paths.append(path)
        return path

    def analyze_with_path(self, path):
        with patch.dict(os.environ, {"AI_MOCK_MODE": "true"}), patch(
            "app.services.document_analysis_service.temporary_document_path",
            return_value=temporary_path(path),
        ):
            return analyze_and_save_document(
                self.document_id
            )

    def test_document_extracted_data_is_replaced_on_reanalysis(self):
        riverside_path = self.make_contract_path(RIVERSIDE_CONTRACT)
        westside_path = self.make_contract_path(WESTSIDE_CONTRACT)

        with self.app.app_context():
            first = self.analyze_with_path(riverside_path)
            self.assertTrue(first["success"])

            document = db.session.get(Document, self.document_id)
            project = db.session.get(Project, self.project_id)
            self.assertEqual(
                document.ai_extracted_data["project_name"],
                "Riverside Office Building",
            )
            self.assertEqual(project.name, "Riverside Office Building")

            second = self.analyze_with_path(westside_path)
            self.assertTrue(second["success"])

            document = db.session.get(Document, self.document_id)
            project = db.session.get(Project, self.project_id)
            self.assertEqual(
                document.ai_extracted_data["project_name"],
                "Westside Medical Center",
            )
            self.assertEqual(document.ai_extracted_data["contract_value"], 7250000)
            self.assertEqual(project.name, "Riverside Office Building")


if __name__ == "__main__":
    unittest.main()
