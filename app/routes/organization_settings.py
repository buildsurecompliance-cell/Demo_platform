from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required

from app.decorators import subscription_required
from app.extensions import db
from app.models import (
    ROLE_ADMIN,
    ROLE_OWNER,
)
from app.services.organizations import (
    get_current_organization,
    require_organization_role,
    update_organization_name,
)


organization_settings_bp = Blueprint(
    "organization_settings",
    __name__,
)


@organization_settings_bp.route(
    "/organization/settings",
    methods=["GET", "POST"],
)
@login_required
@subscription_required
def settings():
    organization = get_current_organization()

    if not organization:
        abort(403)

    require_organization_role(
        ROLE_OWNER,
        ROLE_ADMIN,
    )

    if request.method == "POST":
        try:
            update_organization_name(
                organization,
                request.form.get("name"),
            )
            db.session.commit()
            flash(
                "Organization settings updated.",
                "success",
            )
            return redirect(
                url_for("organization_settings.settings")
            )
        except ValueError as error:
            db.session.rollback()
            flash(
                str(error),
                "danger",
            )
        except Exception:
            db.session.rollback()
            flash(
                "Organization settings could not be updated.",
                "danger",
            )

    return render_template(
        "organization_settings.html",
        organization=organization,
    )
