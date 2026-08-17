from functools import wraps

from flask import (
    abort,
    current_app,
    g,
    jsonify,
    render_template,
    request,
)
from flask_login import current_user

from app.services.organizations import (
    can_manage_members,
    get_current_organization,
)
from app.services.plan_capacity import get_organization_plan
from app.services.subscription_service import (
    BILLING_PERIOD_ENDED,
    GRACE_PERIOD_EXPIRED,
    NO_SUBSCRIPTION,
    SUBSCRIPTION_CANCELED,
    SUBSCRIPTION_INACTIVE,
    SUBSCRIPTION_INCOMPLETE,
    SUBSCRIPTION_INCOMPLETE_EXPIRED,
    SUBSCRIPTION_PAUSED,
    SUBSCRIPTION_UNPAID,
    TRIAL_EXPIRED,
    TRIAL_PERIOD_MISSING,
    SubscriptionAccessError,
    require_operational_access,
)


def wants_json_response():
    if request.path.startswith("/api/"):
        return True

    if request.accept_mimetypes.accept_json and not (
        request.accept_mimetypes.accept_html
        and request.accept_mimetypes["text/html"]
        >= request.accept_mimetypes["application/json"]
    ):
        return True

    return False


def subscription_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return current_app.login_manager.unauthorized()

        try:
            access_result = ensure_subscription_access()
        except SubscriptionAccessError as error:
            return subscription_blocked_response(error.decision)

        if hasattr(access_result, "status_code"):
            return access_result

        return view(*args, **kwargs)

    return wrapped


def ensure_subscription_access():
    organization = get_current_organization()

    if not organization:
        if wants_json_response():
            return _raise_no_organization_json()
        abort(403)

    cached = getattr(g, "subscription_access_decision", None)
    cached_organization_id = getattr(
        g,
        "subscription_access_organization_id",
        None,
    )

    if (
        cached
        and cached_organization_id == organization.id
    ):
        if not cached.allowed:
            raise SubscriptionAccessError(cached)
        return cached

    decision = require_operational_access(organization)
    g.subscription_access_decision = decision
    g.subscription_access_organization_id = organization.id
    return decision


def _raise_no_organization_json():
    response = jsonify(
        {
            "error": "subscription_access_denied",
            "reason_code": "NO_ACTIVE_ORGANIZATION",
            "message": "No active Organization is available.",
            "status": "unavailable",
            "trial_end": None,
            "current_period_end": None,
            "grace_period_end": None,
            "cancel_at_period_end": False,
            "cancel_at": None,
            "cancellation_scheduled": False,
        }
    )
    response.status_code = 403
    return response


def subscription_blocked_response(decision):
    if wants_json_response():
        response = jsonify(_decision_payload(decision))
        response.status_code = 403
        return response

    organization = get_current_organization()
    current_plan = (
        get_organization_plan(organization)
        if organization
        else None
    )
    can_manage_billing = bool(
        organization
        and can_manage_members(current_user, organization)
    )

    return (
        render_template(
            "subscription/access_blocked.html",
            decision=decision,
            organization=organization,
            current_plan=current_plan,
            can_manage_billing=can_manage_billing,
            message=_friendly_message(decision),
        ),
        403,
    )


def _decision_payload(decision):
    return {
        "error": "subscription_access_denied",
        "reason_code": decision.reason_code,
        "message": _friendly_message(decision),
        "status": decision.status,
        "trial_end": _isoformat(decision.trial_end),
        "current_period_end": _isoformat(decision.current_period_end),
        "grace_period_end": _isoformat(decision.grace_period_end),
        "cancel_at_period_end": decision.cancel_at_period_end,
        "cancel_at": _isoformat(decision.cancel_at),
        "cancellation_scheduled": decision.cancellation_scheduled,
    }


def _isoformat(value):
    return value.isoformat() if value else None


def _friendly_message(decision):
    messages = {
        NO_SUBSCRIPTION: (
            "Your organization does not currently have an active subscription."
        ),
        SUBSCRIPTION_INACTIVE: (
            "Your organization does not currently have an active subscription."
        ),
        SUBSCRIPTION_INCOMPLETE: (
            "Your subscription setup has not been completed."
        ),
        SUBSCRIPTION_INCOMPLETE_EXPIRED: (
            "Your subscription setup has expired."
        ),
        TRIAL_EXPIRED: (
            "Your trial has ended. Choose a plan to continue using BuildSure."
        ),
        TRIAL_PERIOD_MISSING: (
            "Your subscription trial needs review before access can continue."
        ),
        GRACE_PERIOD_EXPIRED: (
            "Your payment grace period has ended. Update billing to restore access."
        ),
        SUBSCRIPTION_UNPAID: (
            "Your subscription requires payment before operational access can continue."
        ),
        SUBSCRIPTION_PAUSED: (
            "Your subscription is currently paused."
        ),
        SUBSCRIPTION_CANCELED: (
            "Your subscription has ended."
        ),
        BILLING_PERIOD_ENDED: (
            "Your subscription has ended."
        ),
    }

    return messages.get(
        decision.reason_code,
        "Your organization does not have operational access.",
    )
