import re
import logging

from flask import (
    Blueprint,
    current_app,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    abort,
)

from flask_login import (
    current_user,
    login_required,
    login_user,
    logout_user,
)

from werkzeug.security import check_password_hash

from app.extensions import db
from app.models import ROLE_OWNER, User
from app.security import (
    rate_limited,
    safe_redirect_target,
)
from app.services.organizations import (
    can_manage_members,
    create_default_organization_for_user,
    get_current_membership,
    get_current_organization,
    get_valid_invitation,
    get_user_memberships,
    normalize_email,
    resolve_active_organization,
)
from app.services.plan_capacity import (
    get_organization_plan,
    get_organization_usage,
    set_organization_plan,
    validate_plan_key,
)
from app.services.plan_catalog import (
    format_plan_price,
    get_plan_catalog,
)
from app.services.subscription_service import get_access_decision
from app.services.stripe_service import (
    is_stripe_checkout_configured,
    is_stripe_portal_configured,
)

auth_bp = Blueprint(
    "auth",
    __name__,
)

EMAIL_REGEX = re.compile(r"[^@]+@[^@]+\.[^@]+")
logger = logging.getLogger(__name__)


# ==========================
# HOME
# ==========================

@auth_bp.route("/")
def home():

    if current_user.is_authenticated:
        return redirect(url_for("dashboard.dashboard"))

    return redirect(url_for("auth.login"))


# ==========================
# SUBSCRIBE
# ==========================

@auth_bp.route(
    "/subscribe",
    methods=["GET", "POST"]
)
def subscribe():
    if current_user.is_authenticated:
        organization = get_current_organization()

        if not organization:
            return redirect(
                url_for("auth.organization_required")
            )

        membership = get_current_membership()
        can_change_plan = bool(
            membership
            and membership.role == ROLE_OWNER
        )
        can_manage_billing = can_manage_members(
            current_user,
            organization,
        )
        production_mode = current_app.config.get("ENV") == "production"

        if request.method == "POST":
            if production_mode:
                flash(
                    "Online plan changes are not available yet. Contact BuildSure.",
                    "warning",
                )
                abort(403)

            if not can_change_plan:
                abort(403)

            plan_key = request.form.get("plan_key")

            try:
                previous_plan = get_organization_plan(organization)
                normalized_plan_key = validate_plan_key(plan_key)

                if normalized_plan_key == previous_plan.key:
                    flash(
                        f"{previous_plan.name} is already the current plan.",
                        "info",
                    )

                    return redirect(
                        url_for("auth.subscribe")
                    )

                set_organization_plan(
                    organization,
                    normalized_plan_key,
                )
                db.session.commit()
            except ValueError:
                db.session.rollback()
                abort(404)
            except Exception:
                db.session.rollback()
                logger.exception(
                    "Organization plan change failed organization_id=%s user_id=%s",
                    organization.id,
                    current_user.id,
                )
                flash(
                    "Plan could not be changed.",
                    "danger",
                )
                return redirect(
                    url_for("auth.subscribe")
                )

            selected_plan = get_organization_plan(organization)

            flash(
                f"Organization plan changed to {selected_plan.name}.",
                "success",
            )

            usage = get_organization_usage(organization)
            over_project_limit = (
                selected_plan.max_projects is not None
                and usage.project_count > selected_plan.max_projects
            )
            over_subcontractor_limit = (
                selected_plan.max_subcontractors is not None
                and usage.subcontractor_count
                > selected_plan.max_subcontractors
            )

            if over_project_limit or over_subcontractor_limit:
                flash(
                    (
                        "This Organization is above the selected plan limit. "
                        "Existing data is preserved, but new records may be blocked."
                    ),
                    "warning",
                )

            return redirect(
                url_for("auth.subscribe")
            )

        return render_template(
            "subscribe.html",
            organization=organization,
            plans=get_plan_catalog(),
            capacity_usage=get_organization_usage(organization),
            current_plan=get_organization_plan(organization),
            format_plan_price=format_plan_price,
            subscription_access=get_access_decision(organization),
            can_change_plan=can_change_plan,
            can_manage_billing=can_manage_billing,
            production_mode=production_mode,
            stripe_checkout_configured=is_stripe_checkout_configured(),
            stripe_portal_configured=is_stripe_portal_configured(),
            has_stripe_customer=bool(
                organization.subscription
                and organization.subscription.billing_customer_id
            ),
            email_prefill="",
        )

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).lower().strip()

        if not email:
            flash(
                "Email is required.",
                "danger"
            )
            return redirect(
                url_for("auth.subscribe")
            )

        if not EMAIL_REGEX.match(email):
            flash(
                "Invalid email address.",
                "danger"
            )
            return redirect(
                url_for("auth.subscribe")
            )

        flash(
            "Account setup started. Now create your account.",
            "success"
        )

        return redirect(
            url_for(
                "auth.register",
                email=email
            )
        )

    email_prefill = request.args.get(
        "email",
        ""
    )

    return render_template(
        "subscribe.html",
        email_prefill=email_prefill,
        organization=None,
        plans=[],
    )


# ==========================
# REGISTER
# ==========================

@auth_bp.route(
    "/register",
    methods=["GET", "POST"]
)
@rate_limited("REGISTER_RATE_LIMIT")
def register():

    email_prefill = request.args.get(
        "email",
        ""
    ).lower().strip()
    invitation_token = (
        request.args.get("invitation_token")
        or request.form.get("invitation_token")
        or ""
    ).strip()
    invitation = None

    if invitation_token:
        invitation = get_valid_invitation(invitation_token)

        if not invitation:
            abort(404)

        email_prefill = invitation.email

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).lower().strip()

        password = request.form.get(
            "password",
            ""
        )

        if not email:

            flash(
                "Email is required",
                "danger"
            )

            return render_template(
                "register.html",
                email_prefill=email,
                invitation_token=invitation_token,
            )

        if not EMAIL_REGEX.match(email):

            flash(
                "Invalid email address",
                "danger"
            )

            return render_template(
                "register.html",
                email_prefill=email,
                invitation_token=invitation_token,
            )

        existing_user = User.query.filter_by(
            email=email
        ).first()

        if existing_user:

            flash(
                "Email already registered",
                "danger"
            )

            return render_template(
                "register.html",
                email_prefill=email,
                invitation_token=invitation_token,
            )

        if invitation and normalize_email(email) != invitation.email:

            flash(
                "Please register with the invited email address.",
                "danger"
            )

            return render_template(
                "register.html",
                email_prefill=invitation.email,
                invitation_token=invitation_token,
            )

        if len(password) < 8:

            flash(
                "Password must be at least 8 characters",
                "danger"
            )

            return render_template(
                "register.html",
                email_prefill=email,
                invitation_token=invitation_token,
            )

        try:

            new_user = User(
                email=email,
                paid=False,
            )

            new_user.set_password(password)

            db.session.add(new_user)
            db.session.flush()

            if not invitation:
                create_default_organization_for_user(new_user)

            db.session.commit()

        except Exception:

            db.session.rollback()

            logger.exception(
                "Registration failed email=%s remote_addr=%s",
                email,
                request.remote_addr,
            )

            flash(
                "Something went wrong. Please try again.",
                "danger"
            )

            return render_template(
                "register.html",
                email_prefill=email,
                invitation_token=invitation_token,
            )

        if invitation:
            login_user(
                new_user,
                remember=False,
            )

            flash(
                "Account created. Please accept the invitation.",
                "success"
            )

            return redirect(
                url_for(
                    "team.accept_invitation_route",
                    token=invitation_token,
                )
            )

        flash(
            "Account created successfully! You can now log in.",
            "success"
        )

        return redirect(
            url_for("auth.login")
        )

    return render_template(
        "register.html",
        email_prefill=email_prefill,
        invitation_token=invitation_token,
    )


# ==========================
# LOGIN
# ==========================

@auth_bp.route(
    "/login",
    methods=["GET", "POST"]
)
@rate_limited("LOGIN_RATE_LIMIT")
def login():

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).lower().strip()

        password = request.form.get(
            "password",
            ""
        )

        if not email or not password:

            flash(
                "Email and password are required.",
                "danger"
            )

            return render_template(
                "login.html",
                email=email
            )

        user = User.query.filter_by(
            email=email
        ).first()

        if user and check_password_hash(
            user.password_hash,
            password
        ):

            login_user(
                user,
                remember=False
            )

            if not get_user_memberships(user):
                session.pop("organization_id", None)
                if user.last_active_organization_id:
                    user.last_active_organization_id = None
                    db.session.commit()

                flash(
                    "You do not belong to an Organization yet. Use an invitation link or contact your Organization owner.",
                    "warning"
                )

                return redirect(
                    url_for("auth.organization_required")
                )

            resolve_active_organization(user)
            db.session.commit()

            next_page = request.args.get(
                "next"
            )

            return redirect(
                safe_redirect_target(next_page)
            )

        logger.warning(
            "Failed login attempt email=%s remote_addr=%s",
            email,
            request.remote_addr,
        )

        flash(
            "Invalid credentials",
            "danger"
        )

    return render_template(
        "login.html"
    )


@auth_bp.route("/organization-required")
@login_required
def organization_required():
    if get_user_memberships(current_user):
        return redirect(
            url_for("dashboard.dashboard")
        )

    return render_template(
        "organization_required.html"
    )


# ==========================
# LOGOUT
# ==========================

@auth_bp.route(
    "/logout",
    methods=["POST"],
)
@login_required
def logout():

    logout_user()

    session.clear()
    session.modified = True

    flash(
        "You have been logged out.",
        "info"
    )

    return redirect(
        url_for("auth.login")
    )
