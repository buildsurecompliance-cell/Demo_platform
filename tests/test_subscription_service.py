import os
import unittest

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Organization, Subscription, User
from app.services.organizations import create_default_organization_for_user
from app.services.plan_capacity import (
    ENTERPRISE,
    PROJECT_LIMIT_REACHED,
    require_project_capacity,
    set_organization_plan,
)
from app.services.subscription_service import (
    ACCESS_ACTIVE,
    ACCESS_PAST_DUE_GRACE_PERIOD,
    ACCESS_TRIALING,
    BILLING_PERIOD_ENDED,
    BILLING_PERIOD_MISSING,
    GRACE_PERIOD_EXPIRED,
    INVALID_BILLING_PERIOD,
    INVALID_BILLING_PROVIDER,
    INVALID_SUBSCRIPTION_STATUS,
    INVALID_TRIAL_PERIOD,
    MISSING_PERIOD_END,
    NO_SUBSCRIPTION,
    PROVIDER_INTERNAL,
    PROVIDER_STRIPE,
    STATUS_ACTIVE,
    STATUS_CANCELED,
    STATUS_INACTIVE,
    STATUS_INCOMPLETE,
    STATUS_PAST_DUE,
    STATUS_PAUSED,
    STATUS_TRIALING,
    STATUS_UNPAID,
    SUBSCRIPTION_CANCELED,
    SUBSCRIPTION_INACTIVE,
    SUBSCRIPTION_INCOMPLETE,
    SUBSCRIPTION_PAUSED,
    SUBSCRIPTION_UNPAID,
    TRIAL_EXPIRED,
    TRIAL_PERIOD_MISSING,
    VALID_BILLING_PROVIDERS,
    VALID_SUBSCRIPTION_STATUSES,
    SubscriptionAccessDecision,
    SubscriptionAccessError,
    SubscriptionValidationError,
    activate_subscription,
    cancel_subscription,
    get_access_decision,
    get_or_create_subscription,
    get_subscription,
    has_operational_access,
    is_cancellation_scheduled,
    mark_past_due,
    reactivate_subscription,
    require_operational_access,
    schedule_cancellation,
    scheduled_cancellation_date,
    set_subscription_provider,
    set_subscription_status,
    start_trial,
)
from tests.test_database_migrations import TemporaryMigratedApp


class SubscriptionServiceTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestingConfig)

        with self.app.app_context():
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

            self.organization_id = self.organization.id
            self.other_organization_id = self.other_organization.id
            self.user_id = self.user.id
            self.subscription_id = self.organization.subscription.id
            del self.organization
            del self.other_organization

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def organization(self):
        return db.session.get(Organization, self.organization_id)

    def subscription(self):
        return db.session.get(Subscription, self.subscription_id)

    def test_model_relationships_and_defaults(self):
        with self.app.app_context():
            organization = self.organization()
            subscription = organization.subscription

            self.assertIsNotNone(subscription)
            self.assertEqual(subscription.organization, organization)
            self.assertEqual(subscription.provider, PROVIDER_INTERNAL)
            self.assertEqual(subscription.status, STATUS_ACTIVE)
            self.assertFalse(subscription.cancel_at_period_end)
            self.assertIsNone(subscription.cancel_at)
            self.assertIsNotNone(subscription.created_at)
            self.assertIsNotNone(subscription.updated_at)

    def test_one_subscription_per_organization(self):
        with self.app.app_context():
            duplicate = Subscription(
                organization_id=self.organization_id,
                provider=PROVIDER_INTERNAL,
                status=STATUS_ACTIVE,
            )
            db.session.add(duplicate)

            with self.assertRaises(IntegrityError):
                db.session.commit()

            db.session.rollback()

    def test_two_organizations_can_have_different_subscriptions(self):
        with self.app.app_context():
            self.assertNotEqual(
                self.organization().subscription.id,
                db.session.get(
                    Organization,
                    self.other_organization_id,
                ).subscription.id,
            )

    def test_billing_ids_are_unique_when_present(self):
        with self.app.app_context():
            first = self.organization().subscription
            second = db.session.get(
                Organization,
                self.other_organization_id,
            ).subscription

            first.billing_customer_id = "cus_same"
            second.billing_customer_id = "cus_same"

            with self.assertRaises(IntegrityError):
                db.session.commit()

            db.session.rollback()

            first.billing_subscription_id = "sub_same"
            second.billing_subscription_id = "sub_same"

            with self.assertRaises(IntegrityError):
                db.session.commit()

            db.session.rollback()

    def test_multiple_subscriptions_can_share_price_id(self):
        with self.app.app_context():
            first = self.organization().subscription
            second = db.session.get(
                Organization,
                self.other_organization_id,
            ).subscription
            first.billing_price_id = "price_shared"
            second.billing_price_id = "price_shared"
            db.session.commit()

            self.assertEqual(
                Subscription.query.filter_by(
                    billing_price_id="price_shared",
                ).count(),
                2,
            )

    def test_provider_and_status_registries_are_centralized(self):
        self.assertIn(PROVIDER_INTERNAL, VALID_BILLING_PROVIDERS)
        self.assertIn(PROVIDER_STRIPE, VALID_BILLING_PROVIDERS)
        self.assertIn(STATUS_ACTIVE, VALID_SUBSCRIPTION_STATUSES)
        self.assertIn(STATUS_TRIALING, VALID_SUBSCRIPTION_STATUSES)
        self.assertIn(STATUS_PAST_DUE, VALID_SUBSCRIPTION_STATUSES)

    def test_stripe_provider_is_structural_only(self):
        with self.app.app_context():
            subscription = self.subscription()
            set_subscription_provider(subscription, PROVIDER_STRIPE)
            self.assertEqual(subscription.provider, PROVIDER_STRIPE)

    def test_active_status_allows_access(self):
        with self.app.app_context():
            decision = get_access_decision(self.organization())

            self.assertIsInstance(decision, SubscriptionAccessDecision)
            self.assertTrue(decision.allowed)
            self.assertEqual(decision.status, STATUS_ACTIVE)
            self.assertEqual(decision.reason_code, ACCESS_ACTIVE)

    def test_active_cancellation_before_period_end_allows_access(self):
        with self.app.app_context():
            now = datetime(2026, 7, 28, tzinfo=timezone.utc)
            subscription = self.subscription()
            subscription.current_period_end = now + timedelta(days=1)
            schedule_cancellation(subscription)

            decision = get_access_decision(
                self.organization(),
                now=now,
            )

            self.assertTrue(decision.allowed)
            self.assertTrue(decision.cancel_at_period_end)
            self.assertTrue(decision.cancellation_scheduled)
            self.assertEqual(decision.cancel_at, now + timedelta(days=1))
            self.assertEqual(decision.reason_code, ACCESS_ACTIVE)

    def test_active_future_cancel_at_allows_access_without_legacy_boolean(self):
        with self.app.app_context():
            now = datetime(2026, 8, 11, tzinfo=timezone.utc)
            subscription = self.subscription()
            subscription.provider = PROVIDER_STRIPE
            subscription.status = STATUS_ACTIVE
            subscription.cancel_at_period_end = False
            subscription.current_period_end = now + timedelta(days=26)
            subscription.cancel_at = now + timedelta(days=26)
            subscription.canceled_at = now - timedelta(days=1)

            decision = get_access_decision(self.organization(), now=now)

            self.assertTrue(decision.allowed)
            self.assertEqual(decision.reason_code, ACCESS_ACTIVE)
            self.assertFalse(decision.cancel_at_period_end)
            self.assertTrue(decision.cancellation_scheduled)
            self.assertEqual(decision.cancel_at, now + timedelta(days=26))
            self.assertTrue(is_cancellation_scheduled(subscription, now=now))
            self.assertEqual(
                scheduled_cancellation_date(subscription),
                now + timedelta(days=26),
            )

    def test_canceled_at_alone_does_not_schedule_or_block_active_access(self):
        with self.app.app_context():
            now = datetime(2026, 8, 11, tzinfo=timezone.utc)
            subscription = self.subscription()
            subscription.provider = PROVIDER_STRIPE
            subscription.status = STATUS_ACTIVE
            subscription.cancel_at_period_end = False
            subscription.cancel_at = None
            subscription.canceled_at = now - timedelta(days=1)

            decision = get_access_decision(self.organization(), now=now)

            self.assertTrue(decision.allowed)
            self.assertFalse(decision.cancellation_scheduled)
            self.assertIsNone(decision.cancel_at)

    def test_active_cancellation_after_period_end_blocks_access(self):
        with self.app.app_context():
            now = datetime(2026, 7, 28, tzinfo=timezone.utc)
            subscription = self.subscription()
            subscription.current_period_end = now - timedelta(seconds=1)
            schedule_cancellation(subscription)

            decision = get_access_decision(
                self.organization(),
                now=now,
            )

            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason_code, BILLING_PERIOD_ENDED)

    def test_active_subscription_after_cancel_at_blocks_access(self):
        with self.app.app_context():
            now = datetime(2026, 8, 11, tzinfo=timezone.utc)
            subscription = self.subscription()
            subscription.status = STATUS_ACTIVE
            subscription.cancel_at = now - timedelta(seconds=1)

            decision = get_access_decision(self.organization(), now=now)

            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason_code, BILLING_PERIOD_ENDED)

    def test_trialing_with_valid_trial_allows_access(self):
        with self.app.app_context():
            now = datetime(2026, 7, 28, tzinfo=timezone.utc)
            subscription = self.subscription()
            start_trial(
                subscription,
                trial_start=now - timedelta(days=1),
                trial_end=now + timedelta(days=1),
            )

            decision = get_access_decision(
                self.organization(),
                now=now,
            )

            self.assertTrue(decision.allowed)
            self.assertEqual(decision.reason_code, ACCESS_TRIALING)
            self.assertEqual(decision.trial_end, now + timedelta(days=1))

    def test_trialing_expired_blocks_access(self):
        with self.app.app_context():
            now = datetime(2026, 7, 28, tzinfo=timezone.utc)
            subscription = self.subscription()
            start_trial(
                subscription,
                trial_start=now - timedelta(days=3),
                trial_end=now - timedelta(seconds=1),
            )

            decision = get_access_decision(
                self.organization(),
                now=now,
            )

            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason_code, TRIAL_EXPIRED)

    def test_trialing_without_trial_end_blocks_access(self):
        with self.app.app_context():
            subscription = self.subscription()
            subscription.status = STATUS_TRIALING
            subscription.trial_end = None

            decision = get_access_decision(self.organization())

            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason_code, TRIAL_PERIOD_MISSING)

    def test_start_trial_rejects_invalid_interval(self):
        with self.app.app_context():
            now = datetime(2026, 7, 28, tzinfo=timezone.utc)

            with self.assertRaises(SubscriptionValidationError) as captured:
                start_trial(
                    self.subscription(),
                    trial_start=now,
                    trial_end=now,
                )

            self.assertEqual(captured.exception.reason_code, INVALID_TRIAL_PERIOD)

    def test_past_due_within_grace_period_allows_access(self):
        with self.app.app_context():
            now = datetime(2026, 7, 28, tzinfo=timezone.utc)
            subscription = self.subscription()
            subscription.current_period_end = now - timedelta(days=6)
            mark_past_due(subscription)

            decision = get_access_decision(
                self.organization(),
                now=now,
            )

            self.assertTrue(decision.allowed)
            self.assertEqual(
                decision.reason_code,
                ACCESS_PAST_DUE_GRACE_PERIOD,
            )
            self.assertEqual(
                decision.grace_period_end,
                now + timedelta(days=1),
            )

    def test_past_due_after_grace_period_blocks_access(self):
        with self.app.app_context():
            now = datetime(2026, 7, 28, tzinfo=timezone.utc)
            subscription = self.subscription()
            subscription.current_period_end = now - timedelta(days=8)
            mark_past_due(subscription)

            decision = get_access_decision(
                self.organization(),
                now=now,
            )

            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason_code, GRACE_PERIOD_EXPIRED)

    def test_past_due_without_period_blocks_access(self):
        with self.app.app_context():
            subscription = self.subscription()
            subscription.current_period_end = None
            mark_past_due(subscription)

            decision = get_access_decision(self.organization())

            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason_code, BILLING_PERIOD_MISSING)

    def test_configured_grace_period_is_respected(self):
        class GraceConfig(TestingConfig):
            SUBSCRIPTION_GRACE_PERIOD_DAYS = 3

        app = create_app(GraceConfig)
        with app.app_context():
            db.create_all()
            user = User(email="grace@example.com", paid=True)
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()
            organization = create_default_organization_for_user(user)
            subscription = organization.subscription
            now = datetime(2026, 7, 28, tzinfo=timezone.utc)
            subscription.current_period_end = now - timedelta(days=4)
            mark_past_due(subscription)

            decision = get_access_decision(organization, now=now)

            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason_code, GRACE_PERIOD_EXPIRED)
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def test_blocked_statuses_do_not_allow_access(self):
        cases = {
            STATUS_INACTIVE: SUBSCRIPTION_INACTIVE,
            STATUS_INCOMPLETE: SUBSCRIPTION_INCOMPLETE,
            STATUS_UNPAID: SUBSCRIPTION_UNPAID,
            STATUS_PAUSED: SUBSCRIPTION_PAUSED,
            STATUS_CANCELED: SUBSCRIPTION_CANCELED,
        }

        with self.app.app_context():
            for status, reason_code in cases.items():
                with self.subTest(status=status):
                    subscription = self.subscription()
                    subscription.status = status
                    decision = get_access_decision(self.organization())
                    self.assertFalse(decision.allowed)
                    self.assertEqual(decision.reason_code, reason_code)

    def test_missing_subscription_blocks_without_permissive_fallback(self):
        with self.app.app_context():
            organization = self.organization()
            db.session.delete(organization.subscription)
            db.session.flush()

            decision = get_access_decision(organization)

            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason_code, NO_SUBSCRIPTION)

    def test_production_missing_subscription_is_blocked(self):
        with self.app.app_context():
            organization = self.organization()
            db.session.delete(organization.subscription)
            db.session.flush()

            decision = get_access_decision(organization)

            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason_code, NO_SUBSCRIPTION)

    def test_status_and_provider_validation(self):
        with self.app.app_context():
            subscription = self.subscription()
            set_subscription_status(subscription, STATUS_UNPAID)
            self.assertEqual(subscription.status, STATUS_UNPAID)

            with self.assertRaises(SubscriptionValidationError) as captured:
                set_subscription_status(subscription, "unknown")
            self.assertEqual(
                captured.exception.reason_code,
                INVALID_SUBSCRIPTION_STATUS,
            )

            set_subscription_provider(subscription, PROVIDER_STRIPE)
            self.assertEqual(subscription.provider, PROVIDER_STRIPE)

            with self.assertRaises(SubscriptionValidationError) as captured:
                set_subscription_provider(subscription, "other")
            self.assertEqual(
                captured.exception.reason_code,
                INVALID_BILLING_PROVIDER,
            )

    def test_mutation_functions_do_not_commit(self):
        with self.app.app_context():
            subscription = self.subscription()
            now = datetime(2026, 7, 28, tzinfo=timezone.utc)

            with patch.object(db.session, "commit") as commit:
                activate_subscription(
                    subscription,
                    period_start=now,
                    period_end=now + timedelta(days=30),
                )
                start_trial(
                    subscription,
                    trial_start=now,
                    trial_end=now + timedelta(days=7),
                )
                mark_past_due(subscription)
                schedule_cancellation(
                    subscription,
                    period_end=now + timedelta(days=30),
                )
                cancel_subscription(subscription, canceled_at=now)
                reactivate_subscription(subscription)
                get_or_create_subscription(self.organization())

            commit.assert_not_called()

    def test_activate_rejects_invalid_period(self):
        with self.app.app_context():
            now = datetime(2026, 7, 28, tzinfo=timezone.utc)

            with self.assertRaises(SubscriptionValidationError) as captured:
                activate_subscription(
                    self.subscription(),
                    period_start=now,
                    period_end=now - timedelta(days=1),
                )

            self.assertEqual(
                captured.exception.reason_code,
                INVALID_BILLING_PERIOD,
            )

    def test_schedule_cancellation_requires_period_end(self):
        with self.app.app_context():
            subscription = self.subscription()
            subscription.current_period_end = None

            with self.assertRaises(SubscriptionValidationError) as captured:
                schedule_cancellation(subscription)

            self.assertEqual(captured.exception.reason_code, MISSING_PERIOD_END)

    def test_cancel_subscription_blocks_without_deleting_domain_data(self):
        with self.app.app_context():
            organization = self.organization()
            subscription = organization.subscription
            plan_key = organization.plan_key
            cancel_subscription(subscription)

            decision = get_access_decision(organization)

            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason_code, SUBSCRIPTION_CANCELED)
            self.assertEqual(organization.plan_key, plan_key)
            self.assertIsNotNone(db.session.get(Organization, organization.id))
            self.assertGreaterEqual(len(organization.memberships), 1)

    def test_reactivate_subscription_preserves_plan_and_data(self):
        with self.app.app_context():
            organization = self.organization()
            set_organization_plan(organization, ENTERPRISE)
            subscription = organization.subscription
            cancel_subscription(subscription)
            reactivate_subscription(subscription)

            self.assertEqual(subscription.status, STATUS_ACTIVE)
            self.assertFalse(subscription.cancel_at_period_end)
            self.assertIsNone(subscription.cancel_at)
            self.assertIsNone(subscription.canceled_at)
            self.assertIsNone(subscription.ended_at)
            self.assertEqual(organization.plan_key, ENTERPRISE)

    def test_require_operational_access_returns_or_raises_structured_error(self):
        with self.app.app_context():
            allowed = require_operational_access(self.organization())
            self.assertTrue(allowed.allowed)

            subscription = self.subscription()
            subscription.status = STATUS_UNPAID

            with self.assertRaises(SubscriptionAccessError) as captured:
                require_operational_access(self.organization())

            self.assertEqual(
                captured.exception.decision.reason_code,
                SUBSCRIPTION_UNPAID,
            )
            self.assertEqual(captured.exception.organization_id, self.organization_id)

    def test_access_queries_do_not_mutate_or_commit(self):
        with self.app.app_context():
            subscription = self.subscription()
            before = (
                subscription.status,
                subscription.cancel_at_period_end,
                subscription.cancel_at,
                subscription.current_period_end,
                subscription.updated_at,
            )

            with patch.object(db.session, "commit") as commit:
                has_operational_access(self.organization())
                get_access_decision(self.organization())
                require_operational_access(self.organization())

            commit.assert_not_called()
            after = (
                subscription.status,
                subscription.cancel_at_period_end,
                subscription.cancel_at,
                subscription.current_period_end,
                subscription.updated_at,
            )
            self.assertEqual(before, after)

    def test_subscription_is_organization_scoped(self):
        with self.app.app_context():
            self.subscription().status = STATUS_CANCELED

            self.assertFalse(has_operational_access(self.organization()))
            self.assertTrue(
                has_operational_access(
                    db.session.get(
                        Organization,
                        self.other_organization_id,
                    )
                )
            )

    def test_billing_ids_do_not_grant_cross_tenant_access(self):
        with self.app.app_context():
            first = self.subscription()
            second = db.session.get(
                Organization,
                self.other_organization_id,
            ).subscription
            first.billing_customer_id = "cus_external"
            first.status = STATUS_CANCELED
            second.status = STATUS_ACTIVE

            self.assertFalse(has_operational_access(self.organization()))
            self.assertTrue(
                has_operational_access(
                    db.session.get(
                        Organization,
                        self.other_organization_id,
                    )
                )
            )

    def test_subscription_and_plan_capacity_are_independent(self):
        with self.app.app_context():
            organization = self.organization()
            subscription = organization.subscription
            set_organization_plan(organization, ENTERPRISE)
            subscription.status = STATUS_UNPAID

            self.assertFalse(has_operational_access(organization))
            self.assertEqual(organization.plan_key, ENTERPRISE)
            self.assertIsNone(require_project_capacity(organization).limit)

            set_subscription_status(subscription, STATUS_ACTIVE)
            self.assertTrue(has_operational_access(organization))

    def test_active_subscription_does_not_override_capacity_limit(self):
        with self.app.app_context():
            organization = self.organization()
            for index in range(10):
                from app.models import Project

                db.session.add(
                    Project(
                        name=f"Project {index}",
                        user_id=self.user_id,
                        organization_id=organization.id,
                    )
                )
            db.session.flush()

            with self.assertRaises(Exception) as captured:
                require_project_capacity(organization)

            self.assertEqual(
                getattr(captured.exception, "reason_code", None),
                PROJECT_LIMIT_REACHED,
            )
            self.assertTrue(has_operational_access(organization))

    def test_all_plans_still_have_unlimited_users_and_tools(self):
        from app.services.plan_capacity import PLAN_DEFINITIONS

        for plan in PLAN_DEFINITIONS.values():
            self.assertFalse(hasattr(plan, "max_users"))
            self.assertFalse(hasattr(plan, "features"))
            self.assertFalse(hasattr(plan, "billing_status"))

    def test_create_default_organization_creates_active_subscription(self):
        with self.app.app_context():
            user = User(email="new@example.com", paid=True)
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()

            organization = create_default_organization_for_user(user)

            self.assertIsNotNone(organization.subscription)
            self.assertEqual(organization.subscription.status, STATUS_ACTIVE)
            self.assertEqual(organization.subscription.provider, PROVIDER_INTERNAL)

    def test_get_or_create_subscription_does_not_duplicate(self):
        with self.app.app_context():
            organization = self.organization()
            existing_id = organization.subscription.id

            subscription = get_or_create_subscription(organization)

            self.assertEqual(subscription.id, existing_id)
            self.assertEqual(
                Subscription.query.filter_by(
                    organization_id=organization.id,
                ).count(),
                1,
            )

    def test_subscription_migration_backfills_existing_organizations(self):
        with TemporaryMigratedApp(revision="9d1e2f3a4b5c") as app:
            with app.app_context():
                db.session.execute(
                    text(
                        """
                        INSERT INTO user
                            (id, email, password_hash, paid, timezone)
                        VALUES
                            (9101, 'legacy-billing@example.com', 'hash', 1, 'UTC')
                        """
                    )
                )
                db.session.execute(
                    text(
                        """
                        INSERT INTO organization
                            (id, name, plan_key, created_at, updated_at)
                        VALUES
                            (9201, 'Legacy Org', 'PROFESSIONAL',
                             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """
                    )
                )
                db.session.execute(
                    text(
                        """
                        INSERT INTO organization_membership
                            (organization_id, user_id, role, created_at)
                        VALUES
                            (9201, 9101, 'OWNER', CURRENT_TIMESTAMP)
                        """
                    )
                )
                db.session.commit()

                upgrade(directory="migrations", revision="head")

                inspector = inspect(db.engine)
                self.assertIn("subscription", inspector.get_table_names())
                row = db.session.execute(
                    text(
                        """
                        SELECT provider, status, billing_customer_id,
                               billing_subscription_id, billing_price_id
                        FROM subscription
                        WHERE organization_id = 9201
                        """
                    )
                ).one()
                self.assertEqual(row.provider, PROVIDER_INTERNAL)
                self.assertEqual(row.status, STATUS_ACTIVE)
                self.assertIsNone(row.billing_customer_id)
                self.assertIsNone(row.billing_subscription_id)
                self.assertIsNone(row.billing_price_id)
                self.assertEqual(
                    db.session.execute(
                        text(
                            """
                            SELECT plan_key
                            FROM organization
                            WHERE id = 9201
                            """
                        )
                    ).scalar_one(),
                    "PROFESSIONAL",
                )

    def test_subscription_migration_downgrade_preserves_organizations(self):
        with TemporaryMigratedApp() as app:
            with app.app_context():
                downgrade(directory="migrations", revision="9d1e2f3a4b5c")

                inspector = inspect(db.engine)
                self.assertNotIn("subscription", inspector.get_table_names())
                self.assertIn("organization", inspector.get_table_names())

    def test_subscription_migration_constraints_and_indexes_exist(self):
        with TemporaryMigratedApp() as app:
            with app.app_context():
                inspector = inspect(db.engine)
                indexes = {
                    index["name"]
                    for index in inspector.get_indexes("subscription")
                }
                constraints = {
                    constraint["name"]
                    for constraint in inspector.get_unique_constraints(
                        "subscription"
                    )
                }

                self.assertIn("ix_subscription_status", indexes)
                self.assertIn("ix_subscription_current_period_end", indexes)
                self.assertIn("ix_subscription_trial_end", indexes)
                self.assertIn("uq_subscription_organization_id", constraints)
                self.assertIn(
                    "uq_subscription_billing_customer_id",
                    constraints,
                )
                self.assertIn(
                    "uq_subscription_billing_subscription_id",
                    constraints,
                )


if __name__ == "__main__":
    unittest.main()
