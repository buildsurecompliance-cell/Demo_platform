import os
import sys

from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    load_dotenv(".env.test.local", override=False)

    if os.environ.get("BILLING_E2E_REPORT_ENABLED", "").strip().lower() != "true":
        print(
            "BILLING_E2E_REPORT_DISABLED: set BILLING_E2E_REPORT_ENABLED=true to read local E2E billing state.",
            file=sys.stderr,
        )
        return 2

    from app import create_app
    from app.models import BillingEvent, Organization
    from app.services.billing_observability import mask_external_id
    from app.services.plan_capacity import get_organization_plan
    from app.services.subscription_service import get_access_decision

    app = create_app()
    organization_id = os.environ.get("BILLING_E2E_ORGANIZATION_ID")

    with app.app_context():
        query = Organization.query
        organization = (
            query.filter_by(id=int(organization_id)).first()
            if organization_id
            else query.order_by(Organization.id.desc()).first()
        )

        if not organization:
            print("No local Organization found for E2E report.", file=sys.stderr)
            return 1

        subscription = organization.subscription
        plan = get_organization_plan(organization)
        access = get_access_decision(organization)

        print(f"Organization local ID: {organization.id}")
        print(f"plan_key: {organization.plan_key}")
        print(f"Project limit: {plan.max_projects if plan.max_projects is not None else 'Unlimited'}")
        print(f"Subcontractor limit: {plan.max_subcontractors if plan.max_subcontractors is not None else 'Unlimited'}")
        print(f"Subscription provider: {subscription.provider if subscription else 'None'}")
        print(f"Subscription status: {subscription.status if subscription else 'None'}")
        print(f"current_period_end: {subscription.current_period_end if subscription else 'None'}")
        print(f"cancel_at_period_end: {bool(subscription.cancel_at_period_end) if subscription else False}")
        print(f"operational access: {'allowed' if access.allowed else 'blocked'}")
        print(f"customer linked: {'yes ' + mask_external_id(subscription.billing_customer_id) if subscription and subscription.billing_customer_id else 'no'}")
        print(f"subscription linked: {'yes ' + mask_external_id(subscription.billing_subscription_id) if subscription and subscription.billing_subscription_id else 'no'}")
        print(f"last Stripe sync: {subscription.stripe_last_synced_at if subscription else 'None'}")

        events = (
            BillingEvent.query
            .filter_by(organization_id=organization.id)
            .order_by(BillingEvent.created_at.desc())
            .limit(10)
            .all()
        )
        for event in events:
            print(
                "BillingEvent "
                f"type={event.event_type} "
                f"status={event.status} "
                f"attempts={event.attempt_count}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
