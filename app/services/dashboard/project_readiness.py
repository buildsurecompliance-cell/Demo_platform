from app.services.readiness_service import (
    BLOCKED,
    READY,
    calculate_readiness,
)


def dashboard_readiness_for_project(project):
    if not project.subs:
        return "NO SUBCONTRACTORS"

    saw_checking = False
    saw_attention = False

    for link in project.subs:
        readiness = calculate_readiness(link)

        if readiness["status"] == READY:
            continue

        if is_processing_readiness(readiness):
            saw_checking = True
            continue

        if readiness["status"] == BLOCKED:
            return "BLOCKED"

        saw_attention = True

    if saw_attention:
        return "NEEDS ATTENTION"

    if saw_checking:
        return "CHECKING"

    return "READY"


def is_processing_readiness(readiness):
    codes = {
        reason.get("code")
        for reason in readiness.get("reasons", [])
    }

    if "COI_DOCUMENT_PARTIAL" not in codes:
        return False

    return codes.issubset(
        {
            "COI_DOCUMENT_PARTIAL",
            "COVERAGE_EVIDENCE_MISSING",
        }
    )
