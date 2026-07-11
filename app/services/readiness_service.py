import logging

from datetime import UTC, date, datetime

from app.services.compliance_evidence_service import collect_coi_evidence


READY = "READY"
PENDING = "PENDING"
BLOCKED = "BLOCKED"

BLOCKING = "blocking"
WARNING = "warning"

logger = logging.getLogger(__name__)


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
        _add_reason(
            reasons,
            _reason(
                "INFORMATION_INCOMPLETE",
                "Subcontractor information is incomplete.",
                BLOCKING,
            )
        )

    coi_evidence = []

    if subcontractor:
        coi_evidence = collect_coi_evidence(subcontractor)
        _append_coi_reasons(
            reasons,
            subcontractor,
            today,
            coi_evidence,
        )

    _append_coverage_reasons(
        reasons,
        project_subcontractor,
        project,
        coi_evidence,
    )

    status = _status_from_reasons(reasons)

    return {
        "status": status,
        "reasons": reasons,
        "summary": _summary_for_status(status),
        "checked_at": checked_at.isoformat(),
    }


def _append_coi_reasons(reasons, subcontractor, today, evidence):
    validated_evidence = [
        item
        for item in evidence
        if item.validated
    ]
    rejected_evidence = [
        item
        for item in evidence
        if not item.validated
    ]

    manual_expiration = getattr(
        subcontractor,
        "coi_expiration",
        None,
    )

    if validated_evidence:
        logger.debug(
            "Readiness using validated AI evidence document_id=%s",
            validated_evidence[0].document_id,
        )

        expiration = _resolve_coi_expiration(
            validated_evidence[0],
            manual_expiration,
        )

        _append_coi_expiration_reasons(
            reasons,
            expiration,
            today,
        )
        return

    if rejected_evidence:
        evidence_item = rejected_evidence[0]
        logger.debug(
            "AI evidence rejected document_id=%s reason=%s",
            evidence_item.document_id,
            evidence_item.rejection_code,
        )
        _add_reason(
            reasons,
            _reason(
                evidence_item.rejection_code,
                evidence_item.rejection_message,
                WARNING,
            )
        )

        if manual_expiration:
            logger.debug("Manual value used")
            _append_coi_expiration_reasons(
                reasons,
                manual_expiration,
                today,
            )

        return

    if manual_expiration:
        logger.debug("Readiness falling back to manual COI")
        _append_coi_expiration_reasons(
            reasons,
            manual_expiration,
            today,
        )
        return

    _add_reason(
        reasons,
        _reason(
            "COI_MISSING",
            "Certificate of Insurance information is missing.",
            BLOCKING,
        )
    )


def _resolve_coi_expiration(evidence, manual_expiration):
    evidence_expiration = evidence.value.get("expiration_date")

    if not manual_expiration:
        return evidence_expiration

    manual_date = _as_date(manual_expiration)

    if manual_date != evidence_expiration:
        logger.debug("Readiness detected conflicting COI values")
        return min(
            manual_date,
            evidence_expiration,
        )

    return evidence_expiration


def _append_coi_expiration_reasons(reasons, expiration, today):
    if not expiration:
        _add_reason(
            reasons,
            _reason(
                "COI_MISSING",
                "Certificate of Insurance information is missing.",
                BLOCKING,
            )
        )
        return

    expiration = _as_date(expiration)

    days_left = (
        expiration - today
    ).days

    if days_left < 0:
        _add_reason(
            reasons,
            _reason(
                "COI_EXPIRED",
                "Certificate of Insurance expired.",
                BLOCKING,
            )
        )
        return

    if days_left <= 30:
        _add_reason(
            reasons,
            _reason(
                "COI_EXPIRING_SOON",
                "Certificate of Insurance expires within 30 days.",
                WARNING,
            )
        )


def _append_coverage_reasons(
    reasons,
    project_subcontractor,
    project,
    coi_evidence,
):
    required_coverage = _usable_number(
        getattr(
            project,
            "required_coverage",
            None,
        )
    )

    if required_coverage is None:
        return

    manual_coverage = _usable_number(
        getattr(
            project_subcontractor,
            "coverage_limit",
            None,
        )
    )

    coverage_limit = _resolve_coverage_limit(
        coi_evidence,
        manual_coverage,
    )

    if coverage_limit is None:
        return

    if coverage_limit < required_coverage:
        _add_reason(
            reasons,
            _reason(
                "COVERAGE_INSUFFICIENT",
                "Coverage limit is below the project requirement.",
                BLOCKING,
            )
        )


def _resolve_coverage_limit(coi_evidence, manual_coverage):
    validated_evidence = [
        item
        for item in coi_evidence
        if item.validated
    ]

    if not validated_evidence:
        return manual_coverage

    evidence_coverage = _usable_number(
        validated_evidence[0].value.get("coverage")
    )

    if evidence_coverage is None:
        return manual_coverage

    if manual_coverage is None:
        return evidence_coverage

    if evidence_coverage != manual_coverage:
        logger.debug("Readiness detected conflicting COI coverage values")
        return min(
            evidence_coverage,
            manual_coverage,
        )

    return evidence_coverage


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


def _add_reason(reasons, reason):
    if any(
        existing["code"] == reason["code"]
        for existing in reasons
    ):
        return

    reasons.append(reason)


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
