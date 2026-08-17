import os
import types
import unittest

from datetime import timezone
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Organization, Subscription, User
from app.services.organizations import create_default_organization_for_user
from app.services.plan_capacity import PLAN_ENTERPRISE, PLAN_PROFESSIONAL
from app.services.stripe_price_mapping import (
    STRIPE_PLAN_NOT_SELF_SERVICE,
    STRIPE_PRICE_UNKNOWN,
    StripePriceMappingError,
    get_plan_key_for_stripe_price_id,
    get_stripe_price_id_for_plan,
)
from app.services.stripe_service import (
    STRIPE_EXISTING_SUBSCRIPTION_REQUIRES_PORTAL,
    STRIPE_NOT_CONFIGURED,
    STRIPE_SDK_UNAVAILABLE,
    StripeConfigurationError,
    StripeOperationError,
    create_checkout_session,
    create_customer_portal_session,
    get_or_create_customer,
    is_stripe_configured,
    stripe_timestamp_to_utc_datetime,
)
from app.services.subscription_service import (
    PROVIDER_STRIPE,
    STATUS_ACTIVE,
    STATUS_INACTIVE,
)


class FakeCustomer:
    calls = []
    response = {"id": "cus_created"}

    @classmethod
    def create(cls, **kwargs):
        cls.calls.append(kwargs)
        return cls.response


class FakeCheckoutSession:
    calls = []
    response = {"id": "cs_test", "url": "https://stripe.test/checkout"}

    @classmethod
    def create(cls, **kwargs):
        cls.calls.append(kwargs)
        return cls.response


class FakePortalSession:
    calls = []
    response = {"id": "bps_test", "url": "https://stripe.test/portal"}

    @classmethod
    def create(cls, **kwargs):
        cls.calls.append(kwargs)
        return cls.response


class FakeWebhook:
    calls = []

    @classmethod
    def construct_event(cls, payload, signature_header, webhook_secret):
        cls.calls.append((payload, signature_header, webhook_secret))
        return {"id": "evt_test", "type": "account.updated", "data": {"object": {}}}


class FakeSubscriptionAPI:
    calls = []

    @classmethod
    def retrieve(cls, subscription_id):
        cls.calls.append(subscription_id)
        return {"id": subscription_id}


def fake_stripe_module():
    FakeCustomer.calls = []
    FakeCheckoutSession.calls = []
    FakePortalSession.calls = []
    FakeWebhook.calls = []
    FakeSubscriptionAPI.calls = []

    return types.SimpleNamespace(
        api_key=None,
        api_version=None,
        Customer=FakeCustomer,
        checkout=types.SimpleNamespace(Session=FakeCheckoutSession),
        billing_portal=types.SimpleNamespace(Session=FakePortalSession),
        Webhook=FakeWebhook,
        Subscription=FakeSubscriptionAPI,
    )


class StripeServiceTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config.update(
            BILLING_PROVIDER="stripe",
            STRIPE_SECRET_KEY="sk_test_fake",
            STRIPE_WEBHOOK_SECRET="whsec_fake",
            STRIPE_STARTER_PRICE_ID="price_starter",
            STRIPE_PROFESSIONAL_PRICE_ID="price_professional",
            BILLING_SUCCESS_URL="https://app.test/billing/success",
            BILLING_CANCEL_URL="https://app.test/billing/canceled",
            BILLING_PORTAL_RETURN_URL="https://app.test/subscribe",
        )

        with self.app.app_context():
            db.create_all()
            user = User(email="owner@example.com", paid=False)
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()
            organization = create_default_organization_for_user(user)
            organization.subscription.status = STATUS_INACTIVE
            db.session.commit()

            self.user_id = user.id
            self.organization_id = organization.id
            self.subscription_id = organization.subscription.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def organization(self):
        return db.session.get(Organization, self.organization_id)

    def user(self):
        return db.session.get(User, self.user_id)

    def test_app_imports_without_stripe_configuration_or_sdk(self):
        with self.app.app_context():
            self.app.config.update(
                BILLING_PROVIDER="internal",
                STRIPE_SECRET_KEY=None,
            )
            self.assertFalse(is_stripe_configured())

            with self.assertRaises(StripeConfigurationError) as context:
                get_or_create_customer(self.organization())

        self.assertEqual(context.exception.reason_code, STRIPE_NOT_CONFIGURED)

    def test_missing_sdk_is_reported_only_when_stripe_is_used(self):
        with self.app.app_context():
            with patch(
                "app.services.stripe_service.importlib.import_module",
                side_effect=ImportError("missing stripe"),
            ):
                with self.assertRaises(StripeConfigurationError) as context:
                    get_or_create_customer(self.organization())

        self.assertEqual(context.exception.reason_code, STRIPE_SDK_UNAVAILABLE)

    def test_price_mapping_uses_exact_configured_ids(self):
        with self.app.app_context():
            self.assertEqual(
                get_stripe_price_id_for_plan(PLAN_PROFESSIONAL),
                "price_professional",
            )
            self.assertEqual(
                get_plan_key_for_stripe_price_id("price_professional"),
                PLAN_PROFESSIONAL,
            )

            with self.assertRaises(StripePriceMappingError) as unknown:
                get_plan_key_for_stripe_price_id("price_professional_extra")

            with self.assertRaises(StripePriceMappingError) as enterprise:
                get_stripe_price_id_for_plan(PLAN_ENTERPRISE)

        self.assertEqual(unknown.exception.reason_code, STRIPE_PRICE_UNKNOWN)
        self.assertEqual(
            enterprise.exception.reason_code,
            STRIPE_PLAN_NOT_SELF_SERVICE,
        )

    def test_customer_creation_stores_only_customer_and_provider(self):
        stripe = fake_stripe_module()

        with self.app.app_context():
            organization = self.organization()
            owner = self.user()
            original_plan = organization.plan_key
            original_status = organization.subscription.status

            with patch(
                "app.services.stripe_service.importlib.import_module",
                return_value=stripe,
            ):
                customer_id = get_or_create_customer(
                    organization,
                    owner_user=owner,
                )

            self.assertEqual(customer_id, "cus_created")
            self.assertEqual(organization.subscription.billing_customer_id, "cus_created")
            self.assertEqual(organization.subscription.provider, PROVIDER_STRIPE)
            self.assertEqual(organization.plan_key, original_plan)
            self.assertEqual(organization.subscription.status, original_status)

        call = FakeCustomer.calls[0]
        self.assertEqual(call["metadata"], {"organization_id": str(self.organization_id)})
        self.assertEqual(call["email"], "owner@example.com")
        self.assertIn(
            f"organization:{self.organization_id}",
            call["idempotency_key"],
        )

    def test_customer_creation_reuses_existing_customer(self):
        stripe = fake_stripe_module()

        with self.app.app_context():
            organization = self.organization()
            organization.subscription.billing_customer_id = "cus_existing"
            organization.subscription.provider = PROVIDER_STRIPE

            with patch(
                "app.services.stripe_service.importlib.import_module",
                return_value=stripe,
            ):
                customer_id = get_or_create_customer(organization)

        self.assertEqual(customer_id, "cus_existing")
        self.assertEqual(FakeCustomer.calls, [])

    def test_checkout_session_uses_server_side_price_and_metadata(self):
        stripe = fake_stripe_module()

        with self.app.app_context():
            organization = self.organization()

            with patch(
                "app.services.stripe_service.importlib.import_module",
                return_value=stripe,
            ):
                session = create_checkout_session(
                    organization,
                    plan_key=PLAN_PROFESSIONAL,
                    owner_user=self.user(),
                )

            self.assertEqual(session["url"], "https://stripe.test/checkout")
            self.assertEqual(organization.subscription.billing_customer_id, "cus_created")
            self.assertEqual(organization.subscription.status, STATUS_INACTIVE)

        call = FakeCheckoutSession.calls[0]
        self.assertEqual(call["mode"], "subscription")
        self.assertEqual(call["customer"], "cus_created")
        self.assertEqual(call["line_items"], [{"price": "price_professional", "quantity": 1}])
        self.assertEqual(call["metadata"]["organization_id"], str(self.organization_id))
        self.assertEqual(call["metadata"]["plan_key"], PLAN_PROFESSIONAL)
        self.assertEqual(
            call["subscription_data"]["metadata"],
            call["metadata"],
        )

    def test_existing_active_stripe_subscription_blocks_second_checkout(self):
        stripe = fake_stripe_module()

        with self.app.app_context():
            organization = self.organization()
            organization.subscription.provider = PROVIDER_STRIPE
            organization.subscription.status = STATUS_ACTIVE
            organization.subscription.billing_customer_id = "cus_existing"
            organization.subscription.billing_subscription_id = "sub_existing"
            db.session.commit()

            with patch(
                "app.services.stripe_service.importlib.import_module",
                return_value=stripe,
            ):
                with self.assertRaises(StripeOperationError) as context:
                    create_checkout_session(
                        organization,
                        plan_key=PLAN_PROFESSIONAL,
                        owner_user=self.user(),
                    )

        self.assertEqual(
            context.exception.reason_code,
            STRIPE_EXISTING_SUBSCRIPTION_REQUIRES_PORTAL,
        )
        self.assertEqual(FakeCheckoutSession.calls, [])

    def test_portal_session_requires_existing_customer(self):
        stripe = fake_stripe_module()

        with self.app.app_context():
            organization = self.organization()

            with patch(
                "app.services.stripe_service.importlib.import_module",
                return_value=stripe,
            ):
                with self.assertRaises(Exception):
                    create_customer_portal_session(organization)

            organization.subscription.billing_customer_id = "cus_existing"
            organization.subscription.provider = PROVIDER_STRIPE

            with patch(
                "app.services.stripe_service.importlib.import_module",
                return_value=stripe,
            ):
                session = create_customer_portal_session(organization)

        self.assertEqual(session["url"], "https://stripe.test/portal")
        self.assertEqual(
            FakePortalSession.calls[0]["customer"],
            "cus_existing",
        )

    def test_stripe_timestamps_are_timezone_aware_utc(self):
        with self.app.app_context():
            value = stripe_timestamp_to_utc_datetime(1710000000)

        self.assertIs(value.tzinfo, timezone.utc)


if __name__ == "__main__":
    unittest.main()
