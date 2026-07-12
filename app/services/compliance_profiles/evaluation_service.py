from datetime import date, datetime

from app.services.compliance_evidence_service import collect_coi_evidence
from app.services.compliance_profiles.registry import (
    get_compliance_profile,
)


SATISFIED = "SATISFIED"
MISSING = "MISSING"
PENDING = "PENDING"
INVALID = "INVALID"

BLOCKING = "blocking"
WARNING = "warning"


def evaluate_profile_requirements(
    project_subcontractor,
    profile=None,
    today=None,
    coi_evidence=None,
):
    today = _as_date(today or date.today())
    profile = profile or get_compliance_profile(project_subcontractor)

    requirements = []

    for requirement in profile.requirements:
        if requirement.document_type == "COI":
            requirements.append(
                _evaluate_coi_requirement(
                    project_subcontractor,
                    profile,
                    requirement,
                    today,
                    coi_evidence,
                )
            )
        else:
            requirements.append(
                _evaluate_unsupported_requirement(
                    profile,
                    requirement,
                )
            )

    return {
        "profile_key": profile.key,
        "requirements": requirements,
    }


def _evaluate_unsupported_requirement(profile, requirement):
    severity = BLOCKING if requirement.blocking else WARNING
    status = INVALID if requirement.blocking else PENDING

    return _requirement_result(
        profile,
        requirement,
        status,
        "REQUIREMENT_UNSUPPORTED",
        "Compliance requirement is not supported by this version.",
        severity,
    )


def _evaluate_coi_requirement(
    project_subcontractor,
    profile,
    requirement,
    today,
    coi_evidence,
):
    subcontractor = getattr(
        project_subcontractor,
        "subcontractor",
        None,
    )

    if not subcontractor:
        return _requirement_result(
            profile,
            requirement,
            MISSING,
            "INFORMATION_INCOMPLETE",
            "Subcontractor information is incomplete.",
            BLOCKING,
        )

    evidence = (
        coi_evidence
        if coi_evidence is not None
        else collect_coi_evidence(subcontractor)
    )

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
        expiration = _resolve_coi_expiration(
            validated_evidence[0],
            manual_expiration,
        )
        return _coi_expiration_result(
            profile,
            requirement,
            expiration,
            today,
        )

    if rejected_evidence:
        evidence_item = rejected_evidence[0]

        if manual_expiration:
            manual_result = _coi_expiration_result(
                profile,
                requirement,
                manual_expiration,
                today,
            )

            if manual_result["status"] == INVALID:
                return manual_result

        return _requirement_result(
            profile,
            requirement,
            PENDING,
            evidence_item.rejection_code,
            evidence_item.rejection_message,
            WARNING,
        )

    if manual_expiration:
        return _coi_expiration_result(
            profile,
            requirement,
            manual_expiration,
            today,
        )

    return _requirement_result(
        profile,
        requirement,
        MISSING,
        "COI_MISSING",
        "Certificate of Insurance information is missing.",
        BLOCKING,
    )


def _coi_expiration_result(profile, requirement, expiration, today):
    if not expiration:
        return _requirement_result(
            profile,
            requirement,
            MISSING,
            "COI_MISSING",
            "Certificate of Insurance information is missing.",
            BLOCKING,
        )

    expiration = _as_date(expiration)
    days_left = (
        expiration - today
    ).days

    if days_left < 0:
        return _requirement_result(
            profile,
            requirement,
            INVALID,
            "COI_EXPIRED",
            "Certificate of Insurance expired.",
            BLOCKING,
        )

    if days_left <= 30:
        return _requirement_result(
            profile,
            requirement,
            PENDING,
            "COI_EXPIRING_SOON",
            "Certificate of Insurance expires within 30 days.",
            WARNING,
        )

    return _requirement_result(
        profile,
        requirement,
        SATISFIED,
        None,
        "Certificate of Insurance requirement is satisfied.",
        None,
    )


def _resolve_coi_expiration(evidence, manual_expiration):
    evidence_expiration = evidence.value.get("expiration_date")

    if not manual_expiration:
        return evidence_expiration

    manual_date = _as_date(manual_expiration)

    if manual_date != evidence_expiration:
        return min(
            manual_date,
            evidence_expiration,
        )

    return evidence_expiration


def _requirement_result(
    profile,
    requirement,
    status,
    reason_code,
    message,
    severity,
):
    return {
        "profile_key": profile.key,
        "document_type": requirement.document_type,
        "status": status,
        "required": requirement.required,
        "blocking": requirement.blocking,
        "reason_code": reason_code,
        "message": message,
        "severity": severity,
    }


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()

    return value
