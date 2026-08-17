import os
import sys

from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_E2E_EMAIL = "buildsure-billing-e2e@example.test"
DEFAULT_E2E_ORG = "BuildSure Billing E2E"


def main():
    load_dotenv(".env.test.local", override=False)

    if os.environ.get("BILLING_E2E_PREPARE_ENABLED", "").strip().lower() != "true":
        print(
            "BILLING_E2E_PREPARE_DISABLED: set BILLING_E2E_PREPARE_ENABLED=true to prepare local E2E records.",
            file=sys.stderr,
        )
        return 2

    if os.environ.get("APP_ENV", "").strip().lower() == "production":
        print("Refusing to prepare E2E billing records in production.", file=sys.stderr)
        return 1

    from app import create_app
    from app.extensions import db
    from app.models import Organization, OrganizationMembership, User
    from app.services.organizations import normalize_email
    from app.services.subscription_service import get_or_create_subscription

    email = normalize_email(
        os.environ.get("BILLING_E2E_OWNER_EMAIL") or DEFAULT_E2E_EMAIL
    )
    organization_name = os.environ.get("BILLING_E2E_ORGANIZATION_NAME") or DEFAULT_E2E_ORG
    password = os.environ.get("BILLING_E2E_OWNER_PASSWORD")

    app = create_app()
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        created_user = False
        if not user:
            if not password:
                print(
                    "BILLING_E2E_OWNER_PASSWORD is required when creating the E2E user.",
                    file=sys.stderr,
                )
                return 1
            user = User(email=email, paid=False)
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            created_user = True

        membership = (
            OrganizationMembership.query
            .filter_by(user_id=user.id, role="OWNER")
            .join(Organization)
            .filter(Organization.name == organization_name)
            .first()
        )

        if membership:
            organization = membership.organization
            created_organization = False
        else:
            organization = Organization(name=organization_name)
            db.session.add(organization)
            db.session.flush()
            membership = OrganizationMembership(
                organization_id=organization.id,
                user_id=user.id,
                role="OWNER",
            )
            db.session.add(membership)
            created_organization = True

        subscription = get_or_create_subscription(organization)
        db.session.commit()

        print("Billing E2E records ready.")
        print(f"created_user: {created_user}")
        print(f"created_organization: {created_organization}")
        print(f"organization_id: {organization.id}")
        print(f"user_id: {user.id}")
        print(f"subscription_id: {subscription.id}")
        print("password: not displayed")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
