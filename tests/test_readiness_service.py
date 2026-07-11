import unittest

from datetime import date, datetime

from app.models import Project, ProjectSubcontractor, Subcontractor
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
    ):
        sub = Subcontractor(
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

        return ProjectSubcontractor(
            project=project,
            subcontractor=sub,
            coverage_limit=coverage_limit,
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
