from app.services.compliance.models import (
    ComplianceDecision,
    ComplianceIssue,
)


def validate_coi(
    requirements,
    coi_data,
):

    score = 100

    issues = []
    warnings = []

    # ==========================
    # GENERAL LIABILITY
    # ==========================

    gl = coi_data.get(
        "general_liability_limit"
    )

    try:
        gl = int(
            str(gl)
            .replace(",", "")
            .replace("$", "")
        )
    except Exception:
        gl = 0

    if gl < requirements.general_liability:

        issues.append(
            ComplianceIssue(
                field="general_liability",
                message="General Liability below project requirement."
            )
        )

        score -= 20

    # ==========================
    # AUTO LIABILITY
    # ==========================

    auto = coi_data.get(
        "auto_liability_limit"
    )

    try:
        auto = int(
            str(auto)
            .replace(",", "")
            .replace("$", "")
        )
    except Exception:
        auto = 0

    if auto < requirements.auto_liability:

        issues.append(
            ComplianceIssue(
                field="auto_liability",
                message="Auto Liability below project requirement."
            )
        )

        score -= 15

    # ==========================
    # UMBRELLA
    # ==========================

    umbrella = coi_data.get(
        "umbrella_limit"
    )

    try:
        umbrella = int(
            str(umbrella)
            .replace(",", "")
            .replace("$", "")
        )
    except Exception:
        umbrella = 0

    if umbrella < requirements.umbrella:

        warnings.append(
            ComplianceIssue(
                field="umbrella",
                message="Umbrella coverage below recommended value.",
                severity="warning"
            )
        )

        score -= 5

    # ==========================
    # WORKERS COMP
    # ==========================

    if (
        requirements.workers_comp
        and
        not coi_data.get("workers_compensation")
    ):

        issues.append(
            ComplianceIssue(
                field="workers_compensation",
                message="Workers Compensation missing."
            )
        )

        score -= 20

    # ==========================
    # ADDITIONAL INSURED
    # ==========================

    if (
        requirements.additional_insured
        and
        not coi_data.get("additional_insured")
    ):

        issues.append(
            ComplianceIssue(
                field="additional_insured",
                message="Additional Insured endorsement missing."
            )
        )

        score -= 15

    # ==========================
    # WAIVER
    # ==========================

    if (
        requirements.waiver_of_subrogation
        and
        not coi_data.get("waiver_of_subrogation")
    ):

        issues.append(
            ComplianceIssue(
                field="waiver_of_subrogation",
                message="Waiver of Subrogation missing."
            )
        )

        score -= 10

    # ==========================
    # PRIMARY
    # ==========================

    if (
        requirements.primary_non_contributory
        and
        not coi_data.get("primary_non_contributory")
    ):

        issues.append(
            ComplianceIssue(
                field="primary_non_contributory",
                message="Primary & Non-Contributory endorsement missing."
            )
        )

        score -= 10

    # ==========================
    # FINAL STATUS
    # ==========================

    if issues:

        status = "Blocked"

    elif warnings:

        status = "Pending"

    else:

        status = "Ready"

    return ComplianceDecision(

        status=status,

        score=max(score, 0),

        issues=issues,

        warnings=warnings,

        extracted_data=coi_data,
    )