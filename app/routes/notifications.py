import logging
from datetime import datetime, timezone

from flask import (
    Blueprint,
    flash,
    redirect,
    url_for,
)

from flask_login import (
    current_user,
    login_required,
)

from app.extensions import db

from app.models import (
    Subcontractor,
)

from app.services.notifications.email_service import (
    send_email_reminder,
)


notifications_bp = Blueprint(
    "notifications",
    __name__,
)
logger = logging.getLogger(__name__)


# ==========================
# MANUAL REMINDER
# ==========================

@notifications_bp.route(
    "/send_reminder/<int:sub_id>",
    methods=["POST"]
)
@login_required
def send_reminder(sub_id):

    sub = Subcontractor.query.filter_by(
        id=sub_id,
        user_id=current_user.id
    ).first_or_404()

    if not sub.email:

        flash(
            "Subcontractor does not have an email.",
            "danger"
        )

        return redirect(
            url_for("dashboard.dashboard")
        )

    subject = "COI Expiration Reminder"

    expiration = sub.coi_expiration

    message = f"""
Hello {sub.name},

This is a reminder that your Certificate of Insurance expires on {expiration}.

Please upload an updated COI to remain compliant.

Thank you,

BuildSure Compliance
"""

    sent = send_email_reminder(
        sub.email,
        subject,
        message
    )

    if sent:

        try:

            sub.last_reminder_sent = datetime.now(
                timezone.utc
            )

            db.session.commit()

            flash(
                "Reminder sent successfully.",
                "success"
            )

        except Exception:

            db.session.rollback()

            logger.exception(
                "Reminder timestamp update failed sub_id=%s",
                sub.id,
            )

            flash(
                "Reminder sent but failed to record it.",
                "warning"
            )

    else:

        flash(
            "Failed to send reminder.",
            "danger"
        )

    return redirect(
        url_for("dashboard.dashboard")
    )
