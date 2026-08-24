from datetime import datetime


ANALYSIS_REVIEW_CODES = {
    "COI_DOCUMENT_UNREADABLE",
    "COI_LOW_CONFIDENCE",
    "COI_VALIDATOR_FAILED",
    "AI_CONFIDENCE_LOW",
    "AI_VALIDATION_FAILED",
}


def primary_issue_label(
    readiness,
    project,
    subcontractor,
    evidence=None,
    coverage_label=None,
):
    reason_codes = {
        reason.get("code")
        for reason in readiness.get("reasons", [])
    }
    coverage_label = coverage_label or _default_coverage_label

    if reason_codes & ANALYSIS_REVIEW_CODES:
        return "COI analysis needs review"

    if "COI_MISSING" in reason_codes:
        return "COI missing"

    if "COI_EXPIRED" in reason_codes:
        expiration = coi_expiration_for_issue(subcontractor, evidence)
        if expiration:
            return f"COI expired {_date_with_year_label(expiration)}"

        return "COI expired"

    if "COVERAGE_INSUFFICIENT" in reason_codes:
        current = coi_coverage_for_issue(evidence)
        required = getattr(project, "required_coverage", None)
        if current is not None and required:
            return (
                f"GL {coverage_label(current)} / "
                f"Required {coverage_label(required)}"
            )

        return "GL coverage below requirement"

    if "COI_DOCUMENT_PARTIAL" in reason_codes:
        return "COI analysis incomplete"

    if "COVERAGE_EVIDENCE_MISSING" in reason_codes:
        return "Validated GL coverage missing"

    if "COI_EXPIRING_SOON" in reason_codes:
        expiration = coi_expiration_for_issue(subcontractor, evidence)
        if expiration:
            return f"COI expires {_date_with_year_label(expiration)}"

        return "COI expiring soon"

    if readiness.get("reasons"):
        return readiness["reasons"][0].get(
            "message",
            "Compliance needs review.",
        )

    return "Compliance needs review."


def coi_expiration_for_issue(subcontractor, evidence=None):
    if evidence:
        expiration = evidence.value.get("expiration_date")
        if expiration:
            return expiration

    return getattr(subcontractor, "coi_expiration", None)


def coi_coverage_for_issue(evidence=None):
    if not evidence:
        return None

    return evidence.value.get("coverage")


def _date_with_year_label(value):
    if not value:
        return ""

    if isinstance(value, datetime):
        value = value.date()

    if isinstance(value, str):
        return value

    return value.strftime("%b %d, %Y").replace(" 0", " ")


def _default_coverage_label(value):
    if not value:
        return "No minimum"

    amount = int(value)
    if amount % 1_000_000 == 0:
        return f"${amount // 1_000_000}M"

    return f"${amount:,.0f}"
