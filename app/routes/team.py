from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import (
    current_user,
    login_required,
)

from app.extensions import db
from app.models import (
    ORGANIZATION_ROLES,
    ROLE_ADMIN,
    ROLE_OWNER,
)
from app.services.organizations import (
    accept_invitation,
    can_manage_members,
    create_invitation,
    get_current_organization,
    list_members,
    pending_invitations,
)


team_bp = Blueprint(
    "team",
    __name__,
)


@team_bp.route("/team", methods=["GET", "POST"])
@login_required
def team():
    organization = get_current_organization()
    can_manage = can_manage_members(
        current_user,
        organization,
    )
    invitation_link = None

    if request.method == "POST":
        if not can_manage:
            abort(403)

        try:
            invitation, token = create_invitation(
                request.form.get("email"),
                request.form.get("role"),
            )
            db.session.commit()
            if current_app.config.get("ENV") != "production":
                invitation_link = url_for(
                    "team.accept_invitation_route",
                    token=token,
                    _external=True,
                )
            flash(
                "Invitation created.",
                "success",
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
                "Invitation could not be created.",
                "danger",
            )

    return render_template(
        "team.html",
        organization=organization,
        members=list_members(organization),
        invitations=pending_invitations(organization),
        roles=ORGANIZATION_ROLES,
        can_manage=can_manage,
        owner_role=ROLE_OWNER,
        admin_role=ROLE_ADMIN,
        invitation_link=invitation_link,
    )


@team_bp.route(
    "/team/invitations/<token>/accept",
    methods=["GET", "POST"],
)
@login_required
def accept_invitation_route(token):
    if request.method == "GET":
        return render_template(
            "accept_invitation.html"
        )

    try:
        accept_invitation(
            token,
            current_user,
        )
        db.session.commit()
        flash(
            "Invitation accepted.",
            "success",
        )
        return redirect(
            url_for("dashboard.dashboard")
        )
    except Exception:
        db.session.rollback()
        abort(404)
