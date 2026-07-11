import unittest

from datetime import date, datetime

from app.models import Document, Project, ProjectSubcontractor, Subcontractor
from app.services.readiness_service import (
    BLOCKED,
    PENDING,
    READY,
    calculate_readiness,
)
from app.services.mobilization.mobilization_service import (
    calculate_mobilization_status,
)


class ReadinessServiceTest(unittest.TestCase):

    def make_link(
        self,
        coi_expiration,
        coverage_limit=1000000,
        required_coverage=None,
        sub_user_id=1,
        project_user_id=1,
        documents=None,
        subcontractor_id=None,
    ):
        sub = Subcontractor(
            id=subcontractor_id,
            name="Test Sub",
            user_id=sub_user_id,
            coi_expiration=coi_expiration,
        )

        project = Project(
            name="Test Project",
            user_id=project_user_id,
        )

        if required_coverage is not None:
            project.required_coverage = required_coverage

        if documents is not None:
            sub.documents = documents

        return ProjectSubcontractor(
            project=project,
            subcontractor=sub,
            coverage_limit=coverage_limit,
        )

    def make_coi_document(
        self,
        ai_status="analyzed",
        confidence=0.92,
        expiration_date="2026-02-01",
        coverage=1000000,
        issues=None,
        compliance_status="Ready",
        document_id=1,
        version=1,
        uploaded_at=None,
        sub_id=None,
        project_id=None,
        extracted_data=None,
        compliance_result=None,
    ):
        return Document(
            id=document_id,
            document_type="COI",
            filename="coi.pdf",
            version=version,
            uploaded_at=uploaded_at,
            sub_id=sub_id,
            project_id=project_id,
            ai_status=ai_status,
            ai_confidence=confidence,
            ai_extracted_data=extracted_data if extracted_data is not None else {
                "expiration_date": expiration_date,
                "general_liability_limit": coverage,
                "insurance_carrier": "Sample Carrier",
                "policy_number": "POL-123",
                "confidence": confidence,
            },
            ai_compliance_result=compliance_result if compliance_result is not None else {
                "status": compliance_status,
                "issues": issues or [],
                "warnings": [],
                "confidence": confidence,
            },
        )

    def reason_codes(self, result):
        return {
            reason["code"]
            for reason in result["reasons"]
        }

    def test_missing_coi_is_blocked(self):
        link = self.make_link(None)

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], BLOCKED)
        self.assertIn("COI_MISSING", self.reason_codes(result))

    def test_expired_coi_is_blocked(self):
        link = self.make_link(date(2025, 12, 31))

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], BLOCKED)
        self.assertIn("COI_EXPIRED", self.reason_codes(result))

    def test_coi_expiring_within_30_days_is_pending(self):
        link = self.make_link(date(2026, 1, 31))

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], PENDING)
        self.assertIn("COI_EXPIRING_SOON", self.reason_codes(result))

    def test_valid_coi_more_than_30_days_is_ready(self):
        link = self.make_link(date(2026, 2, 1))

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], READY)
        self.assertEqual(result["reasons"], [])

    def test_valid_ai_coi_evidence_can_make_ready_without_manual_date(self):
        document = self.make_coi_document()
        link = self.make_link(
            None,
            documents=[document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], READY)
        self.assertEqual(result["reasons"], [])

    def test_low_confidence_ai_coi_is_pending(self):
        document = self.make_coi_document(confidence=0.5)
        link = self.make_link(
            None,
            documents=[document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], PENDING)
        self.assertIn("AI_CONFIDENCE_LOW", self.reason_codes(result))

    def test_validator_failed_ai_coi_is_pending(self):
        document = self.make_coi_document(
            issues=[
                {
                    "field": "expiration_date",
                    "message": "Expiration date missing.",
                }
            ]
        )
        link = self.make_link(
            None,
            documents=[document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], PENDING)
        self.assertIn("AI_VALIDATION_FAILED", self.reason_codes(result))

    def test_blocked_validator_status_is_pending(self):
        document = self.make_coi_document(compliance_status="Blocked")
        link = self.make_link(
            None,
            documents=[document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], PENDING)
        self.assertIn("AI_VALIDATION_FAILED", self.reason_codes(result))

    def test_unreadable_ai_coi_document_is_pending(self):
        document = self.make_coi_document(ai_status="failed")
        link = self.make_link(
            None,
            documents=[document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], PENDING)
        self.assertIn("COI_DOCUMENT_UNREADABLE", self.reason_codes(result))

    def test_partially_processed_ai_coi_document_is_pending(self):
        document = self.make_coi_document(ai_status="not_analyzed")
        link = self.make_link(
            None,
            documents=[document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], PENDING)
        self.assertIn("COI_DOCUMENT_PARTIAL", self.reason_codes(result))

    def test_unknown_ai_status_is_pending(self):
        document = self.make_coi_document(ai_status="mystery")
        link = self.make_link(
            None,
            documents=[document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], PENDING)
        self.assertIn("COI_DOCUMENT_PARTIAL", self.reason_codes(result))

    def test_manual_coi_is_used_when_no_document_exists(self):
        link = self.make_link(date(2026, 2, 1))

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], READY)

    def test_no_document_and_no_manual_coi_is_blocked(self):
        link = self.make_link(None)

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], BLOCKED)
        self.assertIn("COI_MISSING", self.reason_codes(result))

    def test_conflicting_ai_and_manual_coi_uses_conservative_date(self):
        document = self.make_coi_document(expiration_date="2026-01-02")
        link = self.make_link(
            date(2026, 12, 31),
            documents=[document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], PENDING)
        self.assertIn("COI_EXPIRING_SOON", self.reason_codes(result))

    def test_conflicting_manual_and_ai_coi_uses_manual_when_earlier(self):
        document = self.make_coi_document(expiration_date="2026-12-31")
        link = self.make_link(
            date(2026, 1, 2),
            documents=[document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], PENDING)
        self.assertIn("COI_EXPIRING_SOON", self.reason_codes(result))

    def test_invalid_current_ai_coi_with_valid_manual_coi_stays_pending(self):
        document = self.make_coi_document(ai_status="failed")
        link = self.make_link(
            date(2026, 12, 31),
            documents=[document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], PENDING)
        self.assertIn("COI_DOCUMENT_UNREADABLE", self.reason_codes(result))

    def test_ai_coverage_feeds_coverage_rule_conservatively(self):
        document = self.make_coi_document(coverage=500000)
        link = self.make_link(
            date(2026, 2, 1),
            coverage_limit=2000000,
            required_coverage=1000000,
            documents=[document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], BLOCKED)
        self.assertIn(
            "COVERAGE_INSUFFICIENT",
            self.reason_codes(result),
        )

    def test_manual_coverage_lower_than_ai_is_conservative(self):
        document = self.make_coi_document(coverage=2000000)
        link = self.make_link(
            date(2026, 2, 1),
            coverage_limit=500000,
            required_coverage=1000000,
            documents=[document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], BLOCKED)
        self.assertIn(
            "COVERAGE_INSUFFICIENT",
            self.reason_codes(result),
        )

    def test_old_expired_coi_does_not_override_new_valid_coi(self):
        old_doc = self.make_coi_document(
            expiration_date="2025-12-31",
            document_id=1,
            version=1,
            uploaded_at=datetime(2025, 1, 1),
        )
        new_doc = self.make_coi_document(
            expiration_date="2026-02-01",
            document_id=2,
            version=2,
            uploaded_at=datetime(2026, 1, 1),
        )
        link = self.make_link(
            None,
            documents=[
                old_doc,
                new_doc,
            ],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], READY)

    def test_old_invalid_coi_does_not_override_new_valid_coi(self):
        old_doc = self.make_coi_document(
            ai_status="failed",
            document_id=1,
            version=1,
            uploaded_at=datetime(2025, 1, 1),
        )
        new_doc = self.make_coi_document(
            expiration_date="2026-02-01",
            document_id=2,
            version=2,
            uploaded_at=datetime(2026, 1, 1),
        )
        link = self.make_link(
            None,
            documents=[
                old_doc,
                new_doc,
            ],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], READY)

    def test_new_invalid_coi_does_not_override_old_valid_coi(self):
        old_doc = self.make_coi_document(
            expiration_date="2026-02-01",
            document_id=1,
            version=1,
            uploaded_at=datetime(2025, 1, 1),
        )
        new_doc = self.make_coi_document(
            ai_status="failed",
            document_id=2,
            version=2,
            uploaded_at=datetime(2026, 1, 1),
        )
        link = self.make_link(
            None,
            documents=[
                new_doc,
                old_doc,
            ],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], READY)

    def test_multiple_valid_cois_choose_latest_version(self):
        old_doc = self.make_coi_document(
            expiration_date="2026-01-15",
            document_id=1,
            version=1,
        )
        new_doc = self.make_coi_document(
            expiration_date="2026-02-01",
            document_id=2,
            version=2,
        )
        link = self.make_link(
            None,
            documents=[
                old_doc,
                new_doc,
            ],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], READY)

    def test_multiple_valid_cois_choose_latest_uploaded_at_tiebreaker(self):
        older = self.make_coi_document(
            expiration_date="2026-01-15",
            document_id=1,
            version=1,
            uploaded_at=datetime(2026, 1, 1),
        )
        newer = self.make_coi_document(
            expiration_date="2026-02-01",
            document_id=2,
            version=1,
            uploaded_at=datetime(2026, 1, 2),
        )
        link = self.make_link(
            None,
            documents=[
                older,
                newer,
            ],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], READY)

    def test_multiple_valid_cois_choose_highest_id_tiebreaker(self):
        lower_id = self.make_coi_document(
            expiration_date="2026-01-15",
            document_id=1,
            version=1,
            uploaded_at=datetime(2026, 1, 1),
        )
        higher_id = self.make_coi_document(
            expiration_date="2026-02-01",
            document_id=2,
            version=1,
            uploaded_at=datetime(2026, 1, 1),
        )
        link = self.make_link(
            None,
            documents=[
                lower_id,
                higher_id,
            ],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], READY)

    def test_malformed_json_does_not_raise(self):
        document = self.make_coi_document(
            extracted_data="not-json",
            compliance_result="not-json",
        )
        link = self.make_link(
            None,
            documents=[document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], PENDING)
        self.assertIn("AI_VALIDATION_FAILED", self.reason_codes(result))

    def test_invalid_ai_expiration_date_is_pending(self):
        document = self.make_coi_document(expiration_date="not-a-date")
        link = self.make_link(
            None,
            documents=[document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], PENDING)
        self.assertIn("AI_VALIDATION_FAILED", self.reason_codes(result))

    def test_confidence_string_is_supported(self):
        document = self.make_coi_document(confidence="0.92")
        link = self.make_link(
            None,
            documents=[document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], READY)

    def test_formatted_coverage_is_supported(self):
        document = self.make_coi_document(coverage="$1,000,000 each occurrence")
        link = self.make_link(
            date(2026, 2, 1),
            coverage_limit=2000000,
            required_coverage=1000000,
            documents=[document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], READY)

    def test_project_document_is_ignored_for_subcontractor_coi(self):
        project_document = self.make_coi_document(
            project_id=10,
            document_id=1,
        )
        link = self.make_link(
            None,
            documents=[project_document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], BLOCKED)
        self.assertIn("COI_MISSING", self.reason_codes(result))

    def test_non_coi_document_is_ignored_for_coi_readiness(self):
        document = self.make_coi_document()
        document.document_type = "W9"
        link = self.make_link(
            None,
            documents=[document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], BLOCKED)
        self.assertIn("COI_MISSING", self.reason_codes(result))

    def test_document_from_other_subcontractor_is_ignored(self):
        other_sub_document = self.make_coi_document(
            sub_id=99,
            document_id=1,
        )
        link = self.make_link(
            None,
            documents=[other_sub_document],
            subcontractor_id=1,
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], BLOCKED)
        self.assertIn("COI_MISSING", self.reason_codes(result))

    def test_reasons_do_not_duplicate_codes(self):
        document = self.make_coi_document(ai_status="failed")
        link = self.make_link(
            date(2026, 1, 1),
            documents=[document],
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        codes = [
            reason["code"]
            for reason in result["reasons"]
        ]
        self.assertEqual(
            len(codes),
            len(set(codes)),
        )

    def test_coi_expiring_today_is_pending(self):
        link = self.make_link(date(2026, 1, 1))

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], PENDING)
        self.assertIn("COI_EXPIRING_SOON", self.reason_codes(result))

    def test_coi_expiring_in_31_days_is_ready(self):
        link = self.make_link(date(2026, 2, 1))

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], READY)

    def test_datetime_inputs_are_compared_as_dates(self):
        link = self.make_link(datetime(2026, 2, 1, 12, 0))

        result = calculate_readiness(
            link,
            today=datetime(2026, 1, 1, 8, 0),
        )

        self.assertEqual(result["status"], READY)

    def test_insufficient_coverage_is_blocked_when_required(self):
        link = self.make_link(
            date(2026, 2, 1),
            coverage_limit=500000,
            required_coverage=1000000,
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], BLOCKED)
        self.assertIn(
            "COVERAGE_INSUFFICIENT",
            self.reason_codes(result),
        )

    def test_invalid_coverage_values_do_not_raise_type_error(self):
        link = self.make_link(
            date(2026, 2, 1),
            coverage_limit="unknown",
            required_coverage="1000000",
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], READY)
        self.assertNotIn(
            "COVERAGE_INSUFFICIENT",
            self.reason_codes(result),
        )

    def test_invalid_required_coverage_is_ignored(self):
        link = self.make_link(
            date(2026, 2, 1),
            coverage_limit=1,
            required_coverage="not-a-number",
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], READY)

    def test_multiple_reasons_are_returned(self):
        link = self.make_link(
            date(2026, 1, 15),
            coverage_limit=500000,
            required_coverage=1000000,
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(
            self.reason_codes(result),
            {
                "COI_EXPIRING_SOON",
                "COVERAGE_INSUFFICIENT",
            },
        )

    def test_blocked_has_priority_over_pending(self):
        link = self.make_link(
            date(2026, 1, 15),
            coverage_limit=500000,
            required_coverage=1000000,
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], BLOCKED)

    def test_project_user_does_not_change_link_readiness(self):
        link = self.make_link(
            date(2026, 2, 1),
            sub_user_id=1,
            project_user_id=2,
        )

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        self.assertEqual(result["status"], READY)

    def test_checked_at_is_iso_utc_datetime(self):
        link = self.make_link(date(2026, 2, 1))

        result = calculate_readiness(
            link,
            today=date(2026, 1, 1),
        )

        checked_at = datetime.fromisoformat(result["checked_at"])
        self.assertIsNotNone(checked_at.tzinfo)

    def test_project_mobilization_all_ready(self):
        link = self.make_link(date.today().replace(year=date.today().year + 1))
        project = link.project

        self.assertEqual(
            project.mobilization_status,
            "Ready to Mobilize",
        )

    def test_project_mobilization_pending_without_blocked(self):
        link = self.make_link(date.today())
        project = link.project

        self.assertEqual(
            project.mobilization_status,
            "Pending Compliance",
        )

    def test_project_mobilization_blocked_priority(self):
        pending = self.make_link(date.today())
        blocked = self.make_link(date.today().replace(year=date.today().year - 1))
        project = pending.project
        blocked.project = project
        project.subs = [
            pending,
            blocked,
        ]

        self.assertEqual(
            project.mobilization_status,
            "Blocked",
        )

        self.assertEqual(
            calculate_mobilization_status(project),
            "Not Cleared",
        )

    def test_project_without_subcontractors_preserves_existing_status(self):
        project = Project(
            name="Empty Project",
            user_id=1,
        )

        self.assertEqual(
            project.mobilization_status,
            "Ready to Mobilize",
        )

        self.assertEqual(
            calculate_mobilization_status(project),
            "Not Cleared",
        )


if __name__ == "__main__":
    unittest.main()
