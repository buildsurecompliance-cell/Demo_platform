import os
import unittest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Organization, User
from app.services.organizations import create_default_organization_for_user
from app.services.plan_capacity import (
    ENTERPRISE,
    PLAN_DEFINITIONS,
    PROFESSIONAL,
    STARTER,
)
from app.services.plan_catalog import (
    BILLING_LOOKUP_ENTERPRISE,
    BILLING_LOOKUP_PROFESSIONAL_MONTHLY,
    BILLING_LOOKUP_STARTER_MONTHLY,
    BILLING_LOOKUP_TO_PLAN_KEY,
    UnknownBillingLookupError,
    format_plan_price,
    get_billing_lookup_for_plan,
    get_plan_catalog,
    get_plan_catalog_entry,
    get_plan_key_for_billing_lookup,
    is_self_service_plan,
)


class PlanCatalogTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestingConfig)
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()
            self.owner = User(email="owner@example.com", paid=False)
            self.owner.set_password("password123")
            db.session.add(self.owner)
            db.session.flush()
            self.organization = create_default_organization_for_user(self.owner)
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def csrf_token(self, path="/login"):
        response = self.client.get(path)
        match = __import__("re").search(
            r'name="csrf_token"[^>]*value="([^"]+)"',
            response.get_data(as_text=True),
        )
        self.assertIsNotNone(match)
        return match.group(1)

    def login(self):
        return self.client.post(
            "/login",
            data={
                "email": "owner@example.com",
                "password": "password123",
                "csrf_token": self.csrf_token("/login"),
            },
            follow_redirects=True,
        )

    def test_catalog_matches_capacity_registry_and_has_stable_order(self):
        catalog = get_plan_catalog()

        self.assertEqual(
            [entry.plan_key for entry in catalog],
            [STARTER, PROFESSIONAL, ENTERPRISE],
        )
        for entry in catalog:
            capacity = PLAN_DEFINITIONS[entry.plan_key]
            self.assertEqual(entry.max_projects, capacity.max_projects)
            self.assertEqual(
                entry.max_subcontractors,
                capacity.max_subcontractors,
            )
            self.assertTrue(entry.all_tools)
            self.assertTrue(entry.unlimited_users)
            self.assertIsNone(entry.monthly_price_cents)

    def test_catalog_entries_and_tuple_are_immutable(self):
        catalog = get_plan_catalog()
        starter = catalog[0]

        with self.assertRaises(TypeError):
            catalog[0] = starter
        with self.assertRaises(Exception):
            starter.display_name = "Changed"
        with self.assertRaises(TypeError):
            BILLING_LOOKUP_TO_PLAN_KEY["new_lookup"] = STARTER

    def test_lookup_keys_are_internal_exact_and_do_not_use_substrings(self):
        self.assertEqual(
            get_plan_key_for_billing_lookup(BILLING_LOOKUP_STARTER_MONTHLY),
            STARTER,
        )
        self.assertEqual(
            get_plan_key_for_billing_lookup(
                BILLING_LOOKUP_PROFESSIONAL_MONTHLY
            ),
            PROFESSIONAL,
        )
        self.assertEqual(
            get_plan_key_for_billing_lookup(BILLING_LOOKUP_ENTERPRISE),
            ENTERPRISE,
        )
        self.assertEqual(
            get_billing_lookup_for_plan(PROFESSIONAL),
            BILLING_LOOKUP_PROFESSIONAL_MONTHLY,
        )

        for lookup in (
            "",
            "professional",
            "stripe_price_professional_monthly",
            "starter_monthly_extra",
        ):
            with self.subTest(lookup=lookup):
                with self.assertRaises(UnknownBillingLookupError):
                    get_plan_key_for_billing_lookup(lookup)

    def test_catalog_has_no_feature_gating_or_user_limits(self):
        for entry in get_plan_catalog():
            self.assertFalse(hasattr(entry, "features"))
            self.assertFalse(hasattr(entry, "max_users"))
            self.assertTrue(entry.all_tools)
            self.assertTrue(entry.unlimited_users)

    def test_self_service_and_price_labels_are_explicit_placeholders(self):
        self.assertTrue(is_self_service_plan(STARTER))
        self.assertTrue(is_self_service_plan(PROFESSIONAL))
        self.assertFalse(is_self_service_plan(ENTERPRISE))
        self.assertEqual(format_plan_price(STARTER), "Contact BuildSure")
        self.assertEqual(format_plan_price(PROFESSIONAL), "Contact BuildSure")
        self.assertEqual(format_plan_price(ENTERPRISE), "Contact sales")

    def test_subscribe_template_uses_catalog_without_exposing_lookup_keys(self):
        self.login()

        response = self.client.get("/subscribe")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        for entry in get_plan_catalog():
            self.assertIn(entry.display_name, body)
            self.assertIn(entry.description, body)
            self.assertNotIn(entry.billing_lookup_key, body)
        self.assertNotIn("price_", body)
        self.assertIn("Checkout Unavailable", body)
        self.assertNotIn("$", body)

    def test_catalog_lookup_does_not_commit_or_mutate_organization(self):
        with self.app.app_context():
            organization = Organization.query.first()
            original_plan_key = organization.plan_key

            with self.assertRaises(UnknownBillingLookupError):
                get_plan_key_for_billing_lookup("unknown")

            self.assertEqual(organization.plan_key, original_plan_key)
            self.assertFalse(db.session.new)
            self.assertFalse(db.session.dirty)

    def test_invalid_plan_key_is_rejected(self):
        with self.assertRaises(ValueError):
            get_plan_catalog_entry("TEAM")
        with self.assertRaises(ValueError):
            get_billing_lookup_for_plan("TEAM")


if __name__ == "__main__":
    unittest.main()
