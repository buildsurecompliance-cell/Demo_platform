import logging

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.extensions import csrf, db
from app.models import (
    EVENT_FAILED,
    EVENT_IGNORED,
    EVENT_PROCESSED,
    EVENT_PROCESSING,
)
from app.services.subscription_service import (
    PROVIDER_STRIPE,
    get_access_decision,
)
from app.services.billing_event_service import (
    create_billing_event,
    get_billing_event,
    mark_event_failed,
    mark_event_ignored,
    mark_event_processed,
    mark_event_processing,
    processing_has_expired,
)
from app.services.billing_observability import (
    utc_now,
    mask_external_id,
    safe_reason_code,
)
from app.services.organizations import (
    can_manage_members,
    get_current_organization,
)
from app.services.plan_capacity import get_organization_plan
from app.services.stripe_reconciliation_service import reconcile_subscription
from app.services.stripe_price_mapping import StripePriceMappingError
from app.services.stripe_service import (
    StripeConfigurationError,
    StripeOperationError,
    StripeWebhookError,
    construct_webhook_event,
    create_checkout_session,
    create_customer_portal_session,
    get_stripe_configuration_status,
)
from app.services.stripe_webhook_service import (
    WEBHOOK_EVENT_IGNORED,
    process_stripe_event,
    stripe_event_id,
    stripe_event_type,
)


billing_bp = Blueprint(
    "billing",
    __name__,
    url_prefix="/billing",
)

logger = logging.getLogger(__name__)


@billing_bp.route("/checkout/<plan_key>", methods=["POST"])
@login_required
def create_checkout(plan_key):
    organization = _current_billing_organization()

    try:
        checkout_session = create_checkout_session(
            organization,
            plan_key=plan_key,
            owner_user=current_user,
        )
        db.session.commit()
    except (StripeConfigurationError, StripePriceMappingError):
        db.session.rollback()
        flash(
            "Online checkout is not available yet. Contact BuildSure.",
            "warning",
        )
        return redirect(url_for("auth.subscribe"))
    except StripeOperationError:
        db.session.rollback()
        logger.exception(
            "Stripe checkout failed organization_id=%s user_id=%s",
            organization.id,
            current_user.id,
        )
        flash(
            "Checkout could not be started. Please try again or contact BuildSure.",
            "danger",
        )
        return redirect(url_for("auth.subscribe"))

    return redirect(_object_value(checkout_session, "url"))


@billing_bp.route("/portal", methods=["POST"])
@login_required
def create_portal():
    organization = _current_billing_organization()

    try:
        portal_session = create_customer_portal_session(organization)
        db.session.commit()
    except StripeConfigurationError:
        db.session.rollback()
        flash(
            "Billing portal is not available yet. Contact BuildSure.",
            "warning",
        )
        return redirect(url_for("auth.subscribe"))
    except StripeOperationError:
        db.session.rollback()
        logger.exception(
            "Stripe portal failed organization_id=%s user_id=%s",
            organization.id,
            current_user.id,
        )
        flash(
            "Billing portal could not be opened. Please contact BuildSure.",
            "danger",
        )
        return redirect(url_for("auth.subscribe"))

    return redirect(_object_value(portal_session, "url"))


@billing_bp.route("/checkout/success")
@login_required
def checkout_success():
    return render_template("billing/checkout_success.html")


@billing_bp.route("/checkout/canceled")
@login_required
def checkout_canceled():
    return render_template("billing/checkout_canceled.html")


@billing_bp.route("/status")
@login_required
def status():
    organization = _current_billing_organization()
    subscription = organization.subscription

    return render_template(
        "billing/status.html",
        organization=organization,
        subscription=subscription,
        current_plan=get_organization_plan(organization),
        access_decision=get_access_decision(organization),
        stripe_config=get_stripe_configuration_status(),
        masked_customer_id=mask_external_id(
            subscription.billing_customer_id if subscription else None
        ),
        masked_subscription_id=mask_external_id(
            subscription.billing_subscription_id if subscription else None
        ),
        can_reconcile=bool(
            subscription
            and subscription.provider == PROVIDER_STRIPE
            and subscription.billing_subscription_id
        ),
    )


@billing_bp.route("/reconcile", methods=["POST"])
@login_required
def reconcile():
    organization = _current_billing_organization()

    try:
        reconcile_subscription(organization)
        db.session.commit()
        flash("Billing status reconciled with Stripe.", "success")
    except StripeOperationError as exc:
        db.session.rollback()
        logger.warning(
            "stripe_reconciliation_failed organization_id=%s reason_code=%s",
            organization.id,
            safe_reason_code(exc),
        )
        flash(
            "Billing reconciliation could not be completed. Please review Stripe configuration or try again.",
            "warning",
        )
    except Exception as exc:
        db.session.rollback()
        logger.exception(
            "stripe_reconciliation_failed organization_id=%s reason_code=%s",
            organization.id,
            safe_reason_code(exc),
        )
        flash(
            "Billing reconciliation could not be completed.",
            "danger",
        )

    return redirect(url_for("billing.status"))


@billing_bp.route("/webhook/stripe", methods=["POST"])
@csrf.exempt
def stripe_webhook():
    try:
        event = construct_webhook_event(
            request.get_data(),
            request.headers.get("Stripe-Signature"),
        )
        event_id = stripe_event_id(event)
        event_type = stripe_event_type(event)
    except (StripeConfigurationError, StripeWebhookError):
        return jsonify({"error": "invalid_webhook"}), 400
    except StripeOperationError:
        return jsonify({"error": "invalid_webhook"}), 400

    billing_event = get_billing_event(PROVIDER_STRIPE, event_id)

    if billing_event and billing_event.status in {
        EVENT_PROCESSED,
        EVENT_IGNORED,
    }:
        logger.info(
            "stripe_webhook_duplicate billing_event_id=%s event_type=%s status=%s attempt_count=%s",
            billing_event.id,
            event_type,
            billing_event.status,
            billing_event.attempt_count,
        )
        return jsonify({"status": billing_event.status}), 200

    if (
        billing_event
        and billing_event.status == EVENT_PROCESSING
        and not processing_has_expired(billing_event)
    ):
        logger.info(
            "stripe_webhook_duplicate billing_event_id=%s event_type=%s status=processing attempt_count=%s",
            billing_event.id,
            event_type,
            billing_event.attempt_count,
        )
        return jsonify({"status": EVENT_PROCESSING}), 200

    try:
        if billing_event is None:
            billing_event = create_billing_event(
                provider=PROVIDER_STRIPE,
                external_event_id=event_id,
                event_type=event_type,
            )

        mark_event_processing(billing_event)
        result = process_stripe_event(event, billing_event)

        if result == WEBHOOK_EVENT_IGNORED:
            mark_event_ignored(
                billing_event,
                reason_code=billing_event.error_message,
            )
        else:
            mark_event_processed(billing_event)

        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = get_billing_event(PROVIDER_STRIPE, event_id)
        if existing and existing.status in {
            EVENT_PROCESSED,
            EVENT_IGNORED,
        }:
            return jsonify({"status": existing.status}), 200
        return jsonify({"error": "webhook_processing_failed"}), 500
    except Exception as exc:
        db.session.rollback()
        _record_failed_event(event_id, event_type, exc)
        logger.exception(
            "Stripe webhook processing failed event_id=%s event_type=%s",
            mask_external_id(event_id),
            event_type,
        )
        return jsonify({"error": "webhook_processing_failed"}), 500

    return jsonify({"status": billing_event.status}), 200


def _current_billing_organization():
    organization = get_current_organization()

    if not organization:
        abort(403)

    if not can_manage_members(current_user, organization):
        abort(403)

    return organization


def _record_failed_event(event_id, event_type, exc):
    try:
        billing_event = get_billing_event(PROVIDER_STRIPE, event_id)
        if billing_event is None:
            billing_event = create_billing_event(
                provider=PROVIDER_STRIPE,
                external_event_id=event_id,
                event_type=event_type,
            )
        billing_event.attempt_count = (billing_event.attempt_count or 0) + 1
        billing_event.last_attempt_at = utc_now()
        mark_event_failed(
            billing_event,
            error_message=safe_reason_code(exc),
        )
        db.session.commit()
    except Exception:
        db.session.rollback()


def _object_value(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)

    return getattr(obj, key, default)
