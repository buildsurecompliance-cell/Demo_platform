from datetime import (
    date,
    datetime,
    timezone,
)
import logging

from sqlalchemy.orm import joinedload

from app.extensions import db

from app.models import Subcontractor

from app.services.notifications.email_service import (
    send_email_reminder,
)


logger = logging.getLogger(__name__)

REMINDER_DAYS = {
    90,
    60,
    45,
    30,
    15,
    7,
    3,
    1,
}


# ==========================
# AUTO REMINDER
# ==========================

def check_and_send_auto_reminders_for_all_users():

    today = date.today()

    subs = (
        Subcontractor.query
        .options(
            joinedload(Subcontractor.owner)
        )
        .filter(
            Subcontractor.coi_expiration.isnot(None)
        )
        .filter(
            Subcontractor.email.isnot(None)
        )
        .all()
    )

    reminders_sent = 0

    for sub in subs:

        expiration = sub.coi_expiration

        if isinstance(expiration, datetime):
            expiration = expiration.date()

        days_left = (
            expiration - today
        ).days

        if days_left < 0:
            continue

        if days_left not in REMINDER_DAYS:
            continue

        if sub.last_reminder_sent:
            if sub.last_reminder_sent.date() == today:
                continue

        subject = "COI Expiration Reminder"

        message = f"""
Hello {sub.name},

Your Certificate of Insurance will expire on {expiration}.

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

            sub.last_reminder_sent = datetime.now(
                timezone.utc
            )

            reminders_sent += 1

            logger.info(
                "Reminder sent for subcontractor_id=%s days_left=%s",
                sub.id,
                days_left,
            )

    if reminders_sent > 0:

        try:
            db.session.commit()

        except Exception:
            db.session.rollback()
            logger.exception("Failed to commit reminder updates")

    logger.info(
        "Total reminders sent: %s",
        reminders_sent,
    )
