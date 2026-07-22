import logging

from datetime import UTC, date, datetime

from app.services.compliance_evidence_service import (
    collect_coi_evidence,
    normalize_coverage_amount,
)
from app.services.compliance_profiles import (
    SATISFIED,
    evaluate_profile_requirements,
    get_compliance_profile,
)


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

    profile = get_compliance_profile(project_subcontractor)
    profile_evaluation = evaluate_profile_requirements(
        project_subcontractor,
        profile=profile,
        today=today,
        coi_evidence=coi_evidence,
    )

    _append_profile_requirement_reasons(
        reasons,
        profile_evaluation,
    )

    if subcontractor:
        _log_coi_evidence_selection(
            coi_evidence,
            getattr(
                subcontractor,
                "coi_expiration",
                None,
            ),
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
        "profile_key": profile_evaluation["profile_key"],
        "requirements": profile_evaluation["requirements"],
    }


def _append_profile_requirement_reasons(reasons, profile_evaluation):
    for requirement in profile_evaluation["requirements"]:
        if requirement["status"] == SATISFIED:
            continue

        _add_reason(
            reasons,
            _reason(
                requirement["reason_code"],
                requirement["message"],
                requirement["severity"],
                profile_key=requirement["profile_key"],
                document_type=requirement["document_type"],
                requirement_status=requirement["status"],
            )
        )


def _log_coi_evidence_selection(coi_evidence, manual_expiration):
    validated_evidence = [
        item
        for item in coi_evidence
        if item.validated
    ]
    rejected_evidence = [
        item
        for item in coi_evidence
        if not item.validated
    ]

    if validated_evidence:
        logger.debug(
            "Readiness using validated AI evidence document_id=%s",
            validated_evidence[0].document_id,
        )
        logger.info(
            "Readiness COI expiration source=document_intelligence document_id=%s expiration=%s manual_expiration=%s",
            validated_evidence[0].document_id,
            validated_evidence[0].value.get("expiration_date"),
            manual_expiration,
        )

        if manual_expiration:
            logger.debug("Manual value used")

        return

    if rejected_evidence:
        logger.debug(
            "AI evidence rejected document_id=%s reason=%s",
            rejected_evidence[0].document_id,
            rejected_evidence[0].rejection_code,
        )

        if manual_expiration:
            logger.debug("Manual value used")

        return

    if manual_expiration:
        logger.debug("Readiness falling back to manual COI")
        logger.info(
            "Readiness COI expiration source=manual expiration=%s",
            manual_expiration,
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
    logger.info(
        "Readiness coverage calculation required_coverage=%s manual_coverage=%s current_coverage=%s",
        required_coverage,
        manual_coverage,
        coverage_limit,
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


def _reason(code, message, severity, **metadata):
    reason = {
        "code": code,
        "message": message,
        "severity": severity,
    }

    reason.update(
        {
            key: value
            for key, value in metadata.items()
            if value is not None
        }
    )

    return reason


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
    return normalize_coverage_amount(value)
