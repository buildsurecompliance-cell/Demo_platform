from datetime import UTC, date, datetime


READY = "READY"
PENDING = "PENDING"
BLOCKED = "BLOCKED"

BLOCKING = "blocking"
WARNING = "warning"


def calculate_readiness(project_subcontractor, today=None):
    checked_at = datetime.now(UTC)
    today = _as_date(today or date.today())

    reasons = []

    subcontractor = getattr(
        project_subcontractor,
        "subcontractor",
        None,
    )

    project = getattr(
        project_subcontractor,
        "project",
        None,
    )

    if not subcontractor:
        reasons.append(
            _reason(
                "INFORMATION_INCOMPLETE",
                "Subcontractor information is incomplete.",
                BLOCKING,
            )
        )

    if subcontractor:
        _append_coi_reasons(
            reasons,
            subcontractor,
            today,
        )

    _append_coverage_reasons(
        reasons,
        project_subcontractor,
        project,
    )

    status = _status_from_reasons(reasons)

    return {
        "status": status,
        "reasons": reasons,
        "summary": _summary_for_status(status),
        "checked_at": checked_at.isoformat(),
    }


def _append_coi_reasons(reasons, subcontractor, today):
    expiration = getattr(
        subcontractor,
        "coi_expiration",
        None,
    )

    if not expiration:
        reasons.append(
            _reason(
                "COI_MISSING",
                "Certificate of Insurance information is missing.",
                BLOCKING,
            )
        )
        return

    if isinstance(expiration, datetime):
        expiration = expiration.date()

    days_left = (
        expiration - today
    ).days

    if days_left < 0:
        reasons.append(
            _reason(
                "COI_EXPIRED",
                "Certificate of Insurance expired.",
                BLOCKING,
            )
        )
        return

    if days_left <= 30:
        reasons.append(
            _reason(
                "COI_EXPIRING_SOON",
                "Certificate of Insurance expires within 30 days.",
                WARNING,
            )
        )


def _append_coverage_reasons(reasons, project_subcontractor, project):
    required_coverage = _usable_number(
        getattr(
            project,
            "required_coverage",
            None,
        )
    )

    if required_coverage is None:
        return

    coverage_limit = _usable_number(
        getattr(
            project_subcontractor,
            "coverage_limit",
            None,
        )
    )

    if coverage_limit is None:
        return

    if coverage_limit < required_coverage:
        reasons.append(
            _reason(
                "COVERAGE_INSUFFICIENT",
                "Coverage limit is below the project requirement.",
                BLOCKING,
            )
        )


def _status_from_reasons(reasons):
    if any(
        reason["severity"] == BLOCKING
        for reason in reasons
    ):
        return BLOCKED

    if reasons:
        return PENDING

    return READY


def _summary_for_status(status):
    if status == BLOCKED:
        return "Subcontractor is blocked from mobilization."

    if status == PENDING:
        return "Subcontractor has pending compliance items."

    return "Subcontractor is ready for mobilization."


def _reason(code, message, severity):
    return {
        "code": code,
        "message": message,
        "severity": severity,
    }


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()

    return value


def _usable_number(value):
    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if number <= 0:
        return None

    return number
