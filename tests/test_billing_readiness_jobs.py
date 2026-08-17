import os
import unittest

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Organization, Subcontractor, User
from app.services.notifications.reminder_service import (
    check_and_send_auto_reminders_for_all_users,
)
from app.services.organizations import create_default_organization_for_user
from app.services.subscription_service import (
    STATUS_ACTIVE,
    STATUS_CANCELED,
    STATUS_INACTIVE,
    STATUS_PAST_DUE,
)


class BillingReadinessJobsTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestingConfig)
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def create_subcontractor_for_subscription(
        self,
        email,
        status,
        *,
        current_period_end=None,
    ):
        user = User(email=email, paid=False)
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        organization = create_default_organization_for_user(user)
        subscription = organization.subscription
        subscription.status = status
        subscription.current_period_end = current_period_end

        sub = Subcontractor(
            name=f"Sub {email}",
            email=f"sub-{email}",
            user_id=user.id,
            organization_id=organization.id,
            coi_expiration=date.today() + timedelta(days=30),
        )
        db.session.add(sub)
        db.session.commit()
        return sub.id, organization.id, subscription.id

    def test_auto_reminders_send_only_for_operational_organizations(self):
        with self.app.app_context():
            active_sub_id, active_org_id, active_subscription_id = (
                self.create_subcontractor_for_subscription(
                    "active@example.com",
                    STATUS_ACTIVE,
                )
            )
            blocked_sub_id, blocked_org_id, blocked_subscription_id = (
                self.create_subcontractor_for_subscription(
                    "blocked@example.com",
                    STATUS_CANCELED,
                )
            )

            with patch(
                "app.services.notifications.reminder_service.send_email_reminder",
                return_value=True,
            ) as send_email:
                check_and_send_auto_reminders_for_all_users()

            self.assertEqual(send_email.call_count, 1)
            self.assertIn(
                "sub-active@example.com",
                send_email.call_args.args[0],
            )

            active_sub = db.session.get(Subcontractor, active_sub_id)
            blocked_sub = db.session.get(Subcontractor, blocked_sub_id)
            self.assertIsNotNone(active_sub.last_reminder_sent)
            self.assertIsNone(blocked_sub.last_reminder_sent)

            active_org = db.session.get(
                Organization,
                active_org_id,
            )
            blocked_org = db.session.get(
                Organization,
                blocked_org_id,
            )
            self.assertEqual(active_org.subscription.status, STATUS_ACTIVE)
            self.assertEqual(blocked_org.subscription.status, STATUS_CANCELED)
            self.assertEqual(active_org.subscription.id, active_subscription_id)
            self.assertEqual(blocked_org.subscription.id, blocked_subscription_id)

    def test_past_due_within_grace_sends_and_expired_grace_skips(self):
        with self.app.app_context():
            self.create_subcontractor_for_subscription(
                "grace@example.com",
                STATUS_PAST_DUE,
                current_period_end=(
                    datetime.now(timezone.utc)
                    - timedelta(days=3)
                ),
            )
            self.create_subcontractor_for_subscription(
                "expired-grace@example.com",
                STATUS_PAST_DUE,
                current_period_end=(
                    datetime.now(timezone.utc)
                    - timedelta(days=10)
                ),
            )

            with patch(
                "app.services.notifications.reminder_service.send_email_reminder",
                return_value=True,
            ) as send_email:
                check_and_send_auto_reminders_for_all_users()

            recipients = [
                call.args[0]
                for call in send_email.call_args_list
            ]
            self.assertIn("sub-grace@example.com", recipients)
            self.assertNotIn("sub-expired-grace@example.com", recipients)

    def test_blocked_organization_does_not_interrupt_active_reminders(self):
        with self.app.app_context():
            self.create_subcontractor_for_subscription(
                "inactive@example.com",
                STATUS_INACTIVE,
            )
            self.create_subcontractor_for_subscription(
                "active-after@example.com",
                STATUS_ACTIVE,
            )

            with patch(
                "app.services.notifications.reminder_service.send_email_reminder",
                return_value=True,
            ) as send_email:
                check_and_send_auto_reminders_for_all_users()

            recipients = [
                call.args[0]
                for call in send_email.call_args_list
            ]
            self.assertEqual(recipients, ["sub-active-after@example.com"])


if __name__ == "__main__":
    unittest.main()
