import unittest

from datetime import date, timedelta
from inspect import getsource
from unittest.mock import patch

from app.models import Document, Project, ProjectSubcontractor, Subcontractor
from app.services.compliance_officer import (
    ComplianceAction,
    HIGH,
    LOW,
    MEDIUM,
    generate_compliance_advice,
)
from app.services.compliance_officer import advisor


class ComplianceOfficerServiceTest(unittest.TestCase):

    def make_link(
        self,
        coi_expiration,
        coverage_limit=1000000,
        required_coverage=None,
        documents=None,
    ):
        sub = Subcontractor(
            name="Test Sub",
            user_id=1,
            coi_expiration=coi_expiration,
        )
        project = Project(
            name="Test Project",
            user_id=1,
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
        expiration_date=None,
        coverage=1000000,
        issues=None,
    ):
        expiration_date = expiration_date or (
            date.today() + timedelta(days=60)
        ).isoformat()

        return Document(
            id=1,
            document_type="COI",
            filename="coi.pdf",
            ai_status=ai_status,
            ai_confidence=confidence,
            ai_extracted_data={
                "expiration_date": expiration_date,
                "general_liability_limit": coverage,
                "insurance_carrier": "Sample Carrier",
                "policy_number": "POL-123",
                "confidence": confidence,
            },
            ai_compliance_result={
                "status": "Ready",
                "issues": issues or [],
                "warnings": [],
                "confidence": confidence,
            },
        )

    def action_titles(self, advice):
        return {
            action.title
            for action in advice.actions
        }

    def test_ready_advice(self):
        link = self.make_link(
            date.today() + timedelta(days=60),
        )

        advice = generate_compliance_advice(link)

        self.assertEqual(advice.status, "READY")
        self.assertEqual(advice.priority, LOW)
        self.assertEqual(advice.summary, "Ready for mobilization.")
        self.assertEqual(advice.actions, ())

    def test_missing_coi_advice_is_blocked(self):
        link = self.make_link(None)

        advice = generate_compliance_advice(link)

        self.assertEqual(advice.status, "BLOCKED")
        self.assertEqual(advice.priority, HIGH)
        self.assertEqual(
            advice.summary,
            "Mobilization blocked because a Certificate of Insurance is missing.",
        )
        self.assertIn(
            "Upload a valid Certificate of Insurance.",
            self.action_titles(advice),
        )

    def test_expired_coi_advice_is_blocked(self):
        link = self.make_link(
            date.today() - timedelta(days=1),
        )

        advice = generate_compliance_advice(link)

        self.assertEqual(advice.status, "BLOCKED")
        self.assertEqual(
            advice.summary,
            "Mobilization blocked because the Certificate of Insurance expired.",
        )
        self.assertIn(
            "Request a renewed insurance certificate.",
            self.action_titles(advice),
        )

    def test_processing_coi_advice_is_pending(self):
        document = self.make_coi_document(ai_status="not_analyzed")
        link = self.make_link(
            None,
            documents=[document],
        )

        advice = generate_compliance_advice(link)

        self.assertEqual(advice.status, "PENDING")
        self.assertEqual(advice.priority, MEDIUM)
        self.assertEqual(
            advice.summary,
            "Compliance review is still pending because document analysis has not completed.",
        )
        self.assertIn(
            "Wait until document analysis completes.",
            self.action_titles(advice),
        )

    def test_unreadable_coi_advice_is_pending(self):
        document = self.make_coi_document(ai_status="failed")
        link = self.make_link(
            None,
            documents=[document],
        )

        advice = generate_compliance_advice(link)

        self.assertEqual(advice.status, "PENDING")
        self.assertEqual(
            advice.summary,
            "Compliance review is still pending because the uploaded COI could not be validated.",
        )
        self.assertIn(
            "Replace unreadable document.",
            self.action_titles(advice),
        )

    def test_coverage_insufficient_advice_is_blocked(self):
        link = self.make_link(
            date.today() + timedelta(days=60),
            coverage_limit=500000,
            required_coverage=1000000,
        )

        advice = generate_compliance_advice(link)

        self.assertEqual(advice.status, "BLOCKED")
        self.assertEqual(
            advice.summary,
            "Mobilization blocked because insurance coverage is below the project requirement.",
        )
        self.assertIn(
            "Review insurance coverage.",
            self.action_titles(advice),
        )

    def test_ai_manual_conflict_uses_readiness_conservative_result(self):
        document = self.make_coi_document(
            expiration_date=(
                date.today() + timedelta(days=1)
            ).isoformat(),
        )
        link = self.make_link(
            date.today() + timedelta(days=90),
            documents=[document],
        )

        advice = generate_compliance_advice(link)

        self.assertEqual(advice.status, "PENDING")
        self.assertEqual(advice.priority, MEDIUM)
        self.assertEqual(
            advice.summary,
            "Compliance review is pending because the Certificate of Insurance expires soon.",
        )

    def test_multiple_reasons_generate_multiple_actions(self):
        link = self.make_link(
            date.today() + timedelta(days=10),
            coverage_limit=500000,
            required_coverage=1000000,
        )

        advice = generate_compliance_advice(link)

        self.assertEqual(advice.status, "BLOCKED")
        self.assertEqual(
            self.action_titles(advice),
            {
                "Request a renewed insurance certificate.",
                "Review insurance coverage.",
            },
        )

    def test_actions_are_sorted_by_priority(self):
        readiness = {
            "status": "BLOCKED",
            "reasons": [
                {
                    "code": "COI_EXPIRING_SOON",
                    "message": "COI expires soon.",
                    "severity": "warning",
                },
                {
                    "code": "COVERAGE_INSUFFICIENT",
                    "message": "Coverage is insufficient.",
                    "severity": "blocking",
                },
            ],
        }

        with patch(
            "app.services.compliance_officer.advisor.calculate_readiness",
            return_value=readiness,
        ):
            advice = generate_compliance_advice(object())

        self.assertEqual(advice.actions[0].priority, HIGH)
        self.assertEqual(advice.actions[1].priority, MEDIUM)

    def test_actions_are_not_duplicated(self):
        link = self.make_link(
            date.today() + timedelta(days=10),
        )

        advice = generate_compliance_advice(link)
        action_keys = [
            (
                action.title,
                action.blocking,
            )
            for action in advice.actions
        ]

        self.assertEqual(
            len(action_keys),
            len(set(action_keys)),
        )

    def test_duplicate_action_titles_are_deduped(self):
        readiness = {
            "status": "BLOCKED",
            "reasons": [
                {
                    "code": "COI_EXPIRED",
                    "message": "COI expired.",
                    "severity": "blocking",
                },
                {
                    "code": "COI_EXPIRING_SOON",
                    "message": "COI expires soon.",
                    "severity": "warning",
                },
            ],
        }

        with patch(
            "app.services.compliance_officer.advisor.calculate_readiness",
            return_value=readiness,
        ):
            advice = generate_compliance_advice(object())

        titles = [
            action.title
            for action in advice.actions
        ]
        self.assertEqual(
            titles.count("Request a renewed insurance certificate."),
            1,
        )

    def test_advice_uses_existing_readiness_reasons(self):
        link = self.make_link(None)

        advice = generate_compliance_advice(link)

        self.assertEqual(advice.reasons[0]["code"], "COI_MISSING")
        self.assertIn("severity", advice.reasons[0])
        self.assertNotIn("ai_extracted_data", advice.reasons[0])
        self.assertNotIn("prompt", advice.reasons[0])

    def test_unknown_reason_generates_generic_action(self):
        readiness = {
            "status": "PENDING",
            "reasons": [
                {
                    "code": "FUTURE_REASON",
                    "message": "Future compliance issue.",
                    "severity": "warning",
                },
            ],
        }

        with patch(
            "app.services.compliance_officer.advisor.calculate_readiness",
            return_value=readiness,
        ):
            advice = generate_compliance_advice(object())

        self.assertEqual(advice.status, "PENDING")
        self.assertEqual(advice.actions[0].title, "Review compliance issue.")
        self.assertEqual(
            advice.actions[0].description,
            "Future compliance issue.",
        )

    def test_reason_without_message_uses_safe_fallback(self):
        readiness = {
            "status": "PENDING",
            "reasons": [
                {
                    "code": "FUTURE_REASON",
                    "severity": "warning",
                },
            ],
        }

        with patch(
            "app.services.compliance_officer.advisor.calculate_readiness",
            return_value=readiness,
        ):
            advice = generate_compliance_advice(object())

        self.assertEqual(
            advice.actions[0].description,
            "Review this compliance issue.",
        )

    def test_reason_without_severity_uses_non_blocking_fallback(self):
        readiness = {
            "status": "PENDING",
            "reasons": [
                {
                    "code": "FUTURE_REASON",
                    "message": "Future compliance issue.",
                },
            ],
        }

        with patch(
            "app.services.compliance_officer.advisor.calculate_readiness",
            return_value=readiness,
        ):
            advice = generate_compliance_advice(object())

        self.assertFalse(advice.actions[0].blocking)
        self.assertEqual(advice.actions[0].priority, MEDIUM)

    def test_multiple_blocking_reasons_generate_high_priority_actions(self):
        readiness = {
            "status": "BLOCKED",
            "reasons": [
                {
                    "code": "COI_MISSING",
                    "message": "COI missing.",
                    "severity": "blocking",
                },
                {
                    "code": "COVERAGE_INSUFFICIENT",
                    "message": "Coverage insufficient.",
                    "severity": "blocking",
                },
            ],
        }

        with patch(
            "app.services.compliance_officer.advisor.calculate_readiness",
            return_value=readiness,
        ):
            advice = generate_compliance_advice(object())

        self.assertEqual(advice.status, "BLOCKED")
        self.assertTrue(all(action.priority == HIGH for action in advice.actions))
        self.assertTrue(all(action.blocking for action in advice.actions))

    def test_blocked_and_pending_reason_mix_keeps_blocked_advice(self):
        readiness = {
            "status": "BLOCKED",
            "reasons": [
                {
                    "code": "COVERAGE_INSUFFICIENT",
                    "message": "Coverage insufficient.",
                    "severity": "blocking",
                },
                {
                    "code": "COI_EXPIRING_SOON",
                    "message": "COI expires soon.",
                    "severity": "warning",
                },
            ],
        }

        with patch(
            "app.services.compliance_officer.advisor.calculate_readiness",
            return_value=readiness,
        ):
            advice = generate_compliance_advice(object())

        self.assertEqual(advice.status, "BLOCKED")
        self.assertEqual(advice.priority, HIGH)
        self.assertNotIn("ready", advice.summary.lower())

    def test_pending_with_multiple_reasons_has_pending_summary(self):
        readiness = {
            "status": "PENDING",
            "reasons": [
                {
                    "code": "AI_VALIDATION_FAILED",
                    "message": "Validation failed.",
                    "severity": "warning",
                },
                {
                    "code": "COI_EXPIRING_SOON",
                    "message": "COI expires soon.",
                    "severity": "warning",
                },
            ],
        }

        with patch(
            "app.services.compliance_officer.advisor.calculate_readiness",
            return_value=readiness,
        ):
            advice = generate_compliance_advice(object())

        self.assertEqual(advice.status, "PENDING")
        self.assertIn("pending", advice.summary.lower())
        self.assertNotIn("ready", advice.summary.lower())

    def test_ready_has_no_blocking_actions(self):
        link = self.make_link(
            date.today() + timedelta(days=60),
        )

        advice = generate_compliance_advice(link)

        self.assertEqual(advice.actions, ())
        self.assertFalse(any(action.blocking for action in advice.actions))

    def test_calculate_readiness_is_called_once(self):
        readiness = {
            "status": "READY",
            "reasons": [],
        }

        with patch(
            "app.services.compliance_officer.advisor.calculate_readiness",
            return_value=readiness,
        ) as calculate_mock:
            advice = generate_compliance_advice(object())

        calculate_mock.assert_called_once()
        self.assertEqual(advice.status, "READY")

    def test_advisor_does_not_access_raw_ai_or_openai(self):
        source = getsource(advisor)

        self.assertNotIn("ai_extracted_data", source)
        self.assertNotIn("ai_compliance_result", source)
        self.assertNotIn("OpenAI", source)
        self.assertNotIn("openai", source)

    def test_advice_results_do_not_share_reasons_or_actions(self):
        first = generate_compliance_advice(self.make_link(None))
        second = generate_compliance_advice(self.make_link(None))

        first.reasons[0]["message"] = "Changed locally."

        self.assertNotEqual(
            first.reasons[0]["message"],
            second.reasons[0]["message"],
        )
        self.assertIsNot(first.actions, second.actions)

    def test_readiness_reasons_are_not_modified(self):
        original_reason = {
            "code": "FUTURE_REASON",
            "message": "Original message.",
            "severity": "warning",
        }
        readiness = {
            "status": "PENDING",
            "reasons": [
                original_reason,
            ],
        }

        with patch(
            "app.services.compliance_officer.advisor.calculate_readiness",
            return_value=readiness,
        ):
            advice = generate_compliance_advice(object())

        advice.reasons[0]["message"] = "Changed advice."

        self.assertEqual(
            original_reason["message"],
            "Original message.",
        )

    def test_compliance_action_instances_do_not_share_state(self):
        first = ComplianceAction(
            title="Review compliance issue.",
            description="First.",
            priority=MEDIUM,
            blocking=False,
        )
        second = ComplianceAction(
            title="Review compliance issue.",
            description="Second.",
            priority=MEDIUM,
            blocking=False,
        )

        first_payload = first.to_dict()
        first_payload["description"] = "Changed."

        self.assertEqual(second.description, "Second.")

    def test_to_dict_keeps_public_advice_shape(self):
        link = self.make_link(
            date.today() + timedelta(days=60),
        )

        payload = generate_compliance_advice(link).to_dict()

        self.assertEqual(payload["status"], "READY")
        self.assertEqual(payload["priority"], LOW)
        self.assertEqual(payload["confidence"], 1.0)
        self.assertIn("generated_at", payload)
        self.assertTrue(payload["generated_at"].endswith("+00:00"))


if __name__ == "__main__":
    unittest.main()
