from datetime import (
    date,
    datetime,
    timezone,
)
import logging

from sqlalchemy.orm import joinedload

from app.core.constants import REMINDER_DAYS
from app.extensions import db

from app.models import Subcontractor
from app.services.subscription_service import has_operational_access

from app.services.notifications.email_service import (
    send_email_reminder,
)


logger = logging.getLogger(__name__)

# ==========================
# AUTO REMINDER
# ==========================

def check_and_send_auto_reminders_for_all_users():

    today = date.today()

    subs = (
        Subcontractor.query
        .options(
            joinedload(Subcontractor.owner),
            joinedload(Subcontractor.organization),
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
        if not has_operational_access(sub.organization):
            logger.info(
                "Skipping automatic reminder for organization_id=%s due to billing access",
                sub.organization_id,
            )
            continue

        expiration = sub.coi_expiration

        if isinstance(expiration, datetime):
            expiration = expiration.date()

        days_left = (
            expiration - today
        ).days

        if days_left < 0:
            continue

        threshold = _reminder_threshold_for_days_left(days_left)

        if threshold is None:
            continue

        if _threshold_already_sent(sub, threshold, expiration):
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
            sub.last_reminder_threshold = threshold
            sub.last_reminder_expiration = expiration

            reminders_sent += 1

            logger.info(
                "Reminder sent for subcontractor_id=%s days_left=%s threshold=%s",
                sub.id,
                days_left,
                threshold,
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


def _reminder_threshold_for_days_left(days_left):
    if days_left < 0:
        return None

    eligible_thresholds = [
        threshold
        for threshold in sorted(REMINDER_DAYS)
        if days_left <= threshold
    ]

    if not eligible_thresholds:
        return None

    return eligible_thresholds[0]


def _threshold_already_sent(sub, threshold, expiration):
    last_threshold = getattr(
        sub,
        "last_reminder_threshold",
        None,
    )
    last_expiration = getattr(
        sub,
        "last_reminder_expiration",
        None,
    )

    if last_threshold is not None:
        return (
            last_expiration == expiration
            and last_threshold <= threshold
        )

    if not sub.last_reminder_sent:
        return False

    return sub.last_reminder_sent.date() == date.today()
