import os
import tempfile
import unittest

from unittest.mock import patch


os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.services.document_intelligence.engine import (  # noqa: E402
    analyze_document_intelligence,
)
from app.services.compliance_evidence_service import (  # noqa: E402
    extract_coi_coverage,
    extract_coi_expiration_date,
)


class COIDocumentIntelligenceTest(unittest.TestCase):

    def analyze_text(self, text):
        temp_file = tempfile.NamedTemporaryFile(
            suffix=".pdf",
            mode="w",
            encoding="utf-8",
            delete=False,
        )
        temp_file.write(text)
        temp_file.close()

        try:
            with patch.dict(
                os.environ,
                {"AI_MOCK_MODE": "true"},
            ):
                return analyze_document_intelligence(
                    temp_file.name,
                    "COI",
                )
        finally:
            os.unlink(temp_file.name)

    def test_coi_fields_on_same_line(self):
        result = self.analyze_text(
            """
            Certificate of Insurance
            POLICY EFF: 01/01/2027
            POLICY EXP: 09/01/2027
            Commercial General Liability
            EACH OCCURRENCE $2,000,000
            GENERAL AGGREGATE $4,000,000
            PRODUCTS - COMP/OP AGG $4,000,000
            """
        )

        data = result["extracted_data"]

        self.assertTrue(result["success"])
        self.assertEqual(data["expiration_date"], "2027-09-01")
        self.assertEqual(data["general_liability_limit"], "2000000")
        self.assertEqual(
            data["general_liability"]["each_occurrence"],
            2000000,
        )
        self.assertEqual(
            data["general_liability"]["general_aggregate"],
            4000000,
        )
        self.assertEqual(
            data["general_liability"]["products_completed_operations"],
            4000000,
        )

    def test_coi_fields_on_following_lines(self):
        result = self.analyze_text(
            """
            Certificate of Insurance
            POLICY EXP
            09-01-2027
            POLICY EFF
            01-01-2027
            Limits
            EACH OCCURRENCE
            $2M
            """
        )

        data = result["extracted_data"]

        self.assertTrue(result["success"])
        self.assertEqual(data["expiration_date"], "2027-09-01")
        self.assertEqual(
            data["general_liability"]["expiration_date"],
            "2027-09-01",
        )
        self.assertEqual(data["general_liability_limit"], "2000000")

    def test_multiple_coverages_use_earliest_future_expiration(self):
        result = self.analyze_text(
            """
            Certificate of Insurance
            General Liability POLICY EXP: 12/31/2027
            Automobile Liability POLICY EXP: 09/01/2027
            Umbrella Liability POLICY EXP: 03/01/2028
            EACH OCCURRENCE $2,000,000
            COMBINED SINGLE LIMIT $1,000,000
            UMBRELLA EACH OCCURRENCE $5,000,000
            """
        )

        data = result["extracted_data"]

        self.assertTrue(result["success"])
        self.assertEqual(data["expiration_date"], "2027-09-01")
        self.assertEqual(
            data["general_liability"]["expiration_date"],
            "2027-09-01",
        )
        self.assertEqual(
            data["automobile_liability"]["combined_single_limit"],
            1000000,
        )
        self.assertEqual(
            data["umbrella_liability"]["each_occurrence"],
            5000000,
        )

    def test_dates_out_of_order_do_not_use_policy_eff_as_expiration(self):
        result = self.analyze_text(
            """
            Certificate of Insurance
            POLICY EFF: 01/01/2027
            POLICY EXP: 09/01/2027
            EACH OCCURRENCE 2 million
            """
        )

        data = result["extracted_data"]

        self.assertTrue(result["success"])
        self.assertEqual(data["effective_date"], "2027-01-01")
        self.assertEqual(data["expiration_date"], "2027-09-01")
        self.assertEqual(data["general_liability_limit"], "2000000")

    def test_evidence_service_reads_nested_general_liability_expiration(self):
        expiration = extract_coi_expiration_date(
            {
                "general_liability": {
                    "expiration_date": "09/01/2027",
                },
            }
        )

        self.assertEqual(expiration.isoformat(), "2027-09-01")

    def test_table_layout_does_not_parse_dates_as_coverage(self):
        result = self.analyze_text(
            """
            Certificate of Insurance
            Policy Number
            Policy Effective Date
            Policy Expiration Date
            GL-100
            10/01/2026
            01/31/2027
            $1,000,000 Each Occurrence
            """
        )

        data = result["extracted_data"]

        self.assertTrue(result["success"])
        self.assertEqual(data["expiration_date"], "2027-01-31")
        self.assertEqual(data["general_liability_limit"], "1000000")

    def test_each_occurrence_and_general_aggregate_are_not_confused(self):
        result = self.analyze_text(
            """
            Certificate of Insurance
            POLICY EXP
            04/01/2027
            COMMERCIAL GENERAL LIABILITY
            EACH OCCURRENCE
            $5,000,000
            GENERAL AGGREGATE
            $10,000,000
            PRODUCTS - COMP/OP AGG
            $10,000,000
            """
        )

        data = result["extracted_data"]

        self.assertTrue(result["success"])
        self.assertEqual(
            data["general_liability"]["each_occurrence"],
            5000000,
        )
        self.assertEqual(
            data["general_liability"]["general_aggregate"],
            10000000,
        )
        self.assertEqual(
            data["general_liability"]["products_completed_operations"],
            10000000,
        )
        self.assertEqual(data["general_liability_limit"], "5000000")
        self.assertEqual(data["coverage_limit"], "5000000")
        self.assertEqual(extract_coi_coverage(data), 5000000)

    def test_expired_coi_still_extracts_expiration(self):
        result = self.analyze_text(
            """
            Certificate of Insurance
            POLICY EXP: 01/01/2025
            EACH OCCURRENCE $2,000,000
            """
        )

        self.assertTrue(result["success"])
        self.assertEqual(
            result["extracted_data"]["expiration_date"],
            "2025-01-01",
        )

    def test_workers_compensation_limits_are_structured(self):
        result = self.analyze_text(
            """
            Certificate of Insurance
            POLICY EXP: 09/01/2027
            EACH OCCURRENCE $2,000,000
            WORKERS COMPENSATION AND EMPLOYERS LIABILITY
            E.L. EACH ACCIDENT $1,000,000
            E.L. DISEASE - EA EMPLOYEE $1,000,000
            E.L. DISEASE - POLICY LIMIT $1,000,000
            """
        )

        workers = result["extracted_data"]["workers_compensation"]

        self.assertEqual(workers["each_accident"], 1000000)
        self.assertEqual(workers["disease_each_employee"], 1000000)
        self.assertEqual(workers["disease_policy_limit"], 1000000)

    def test_document_without_required_fields_fails_cleanly(self):
        result = self.analyze_text(
            """
            Certificate of Insurance
            This page intentionally omits policy dates and limits.
            """
        )

        self.assertFalse(result["success"])
        self.assertEqual(
            result["error"],
            "COI fields could not be extracted from the document.",
        )
        self.assertEqual(result["extracted_data"], {})


if __name__ == "__main__":
    unittest.main()
