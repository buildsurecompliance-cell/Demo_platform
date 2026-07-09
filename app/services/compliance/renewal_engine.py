from datetime import date, datetime

from app.core import (
    ComplianceStatus,
    PENDING_RENEWAL_DAYS,
    RENEWAL_REQUIRED_DAYS,
)


def parse_date(value):

    if not value:
        return None

    if isinstance(value, date):
        return value

    try:
        return datetime.strptime(
            value,
            "%Y-%m-%d"
        ).date()

    except Exception:
        return None


def evaluate_renewal_status(
    expiration_date,
    project_end_date=None,
    renewal_required_days=RENEWAL_REQUIRED_DAYS,
    pending_renewal_days=PENDING_RENEWAL_DAYS,
):

    expiration = parse_date(
        expiration_date
    )

    project_end = parse_date(
        project_end_date
    )

    today = date.today()

    if not expiration:

        return {
            "status": ComplianceStatus.BLOCKED.value,
            "renewal_required": False,
            "days_until_expiration": None,
            "next_action": "Upload a valid COI with an expiration date.",
            "message": "COI expiration date was not found.",
        }

    days_until_expiration = (
        expiration - today
    ).days

    if days_until_expiration < 0:

        return {
            "status": ComplianceStatus.BLOCKED.value,
            "renewal_required": True,
            "days_until_expiration": days_until_expiration,
            "next_action": "Request an updated COI immediately.",
            "message": "COI is expired.",
        }

    if days_until_expiration <= pending_renewal_days:

        return {
            "status": ComplianceStatus.PENDING_RENEWAL.value,
            "renewal_required": True,
            "days_until_expiration": days_until_expiration,
            "next_action": "Request renewal before expiration.",
            "message": (
                f"COI expires in {days_until_expiration} days."
            ),
        }

    if days_until_expiration <= renewal_required_days:

        return {
            "status": ComplianceStatus.READY_RENEWAL_REQUIRED.value,
            "renewal_required": True,
            "days_until_expiration": days_until_expiration,
            "next_action": "Schedule COI renewal follow-up.",
            "message": (
                f"COI renewal required in {days_until_expiration} days."
            ),
        }

    if project_end and expiration < project_end:

        return {
            "status": ComplianceStatus.READY_RENEWAL_REQUIRED.value,
            "renewal_required": True,
            "days_until_expiration": days_until_expiration,
            "next_action": "Track future COI renewal during the project.",
            "message": (
                "COI is currently valid but expires before project completion."
            ),
        }

    return {
        "status": ComplianceStatus.READY.value,
        "renewal_required": False,
        "days_until_expiration": days_until_expiration,
        "next_action": "No action required.",
        "message": "COI is valid.",
    }