from datetime import UTC, datetime

from app.services.compliance_evidence_service import collect_coi_evidence
from app.services.compliance_officer.models import (
    ComplianceAction,
    ComplianceAdvice,
)
from app.services.compliance_profiles import get_compliance_profile
from app.services.readiness_service import (
    BLOCKED,
    PENDING,
    READY,
    calculate_readiness,
)


HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"


def generate_compliance_advice(project_subcontractor):
    profile = get_compliance_profile(project_subcontractor)
    evidence = _collect_evidence(project_subcontractor)
    readiness = calculate_readiness(project_subcontractor)

    reasons = tuple(
        dict(reason)
        for reason in readiness.get(
            "reasons",
            [],
        )
    )

    summary = _deterministic_summary(
        readiness["status"],
        reasons,
    )
    actions = _dedupe_actions(
        _actions_for_reasons(
            readiness["status"],
            reasons,
        )
    )

    context = {
        "profile_key": profile.key,
        "evidence_count": len(evidence),
        "readiness": readiness,
    }

    summary = _future_ai_summary_hook(
        summary,
        context,
    )
    actions = _future_ai_actions_hook(
        actions,
        context,
    )

    return ComplianceAdvice(
        status=readiness["status"],
        summary=summary,
        reasons=reasons,
        actions=actions,
        priority=_priority_for_status(readiness["status"]),
        confidence=1.0,
        generated_at=datetime.now(UTC),
    )


def _collect_evidence(project_subcontractor):
    subcontractor = getattr(
        project_subcontractor,
        "subcontractor",
        None,
    )

    if not subcontractor:
        return []

    return collect_coi_evidence(subcontractor)


def _deterministic_summary(status, reasons):
    codes = {
        reason.get("code")
        for reason in reasons
    }

    if status == READY:
        return "Ready for mobilization."

    if status == BLOCKED:
        if "COI_EXPIRED" in codes:
            return (
                "Mobilization blocked because the "
                "Certificate of Insurance expired."
            )

        if "COI_MISSING" in codes:
            return (
                "Mobilization blocked because a "
                "Certificate of Insurance is missing."
            )

        if "COVERAGE_INSUFFICIENT" in codes:
            return (
                "Mobilization blocked because insurance coverage "
                "is below the project requirement."
            )

        return (
            "Mobilization blocked because compliance requirements "
            "are not satisfied."
        )

    if status == PENDING:
        if "COI_DOCUMENT_UNREADABLE" in codes:
            return (
                "Compliance review is still pending because the "
                "uploaded COI could not be validated."
            )

        if "COI_DOCUMENT_PARTIAL" in codes:
            return (
                "Compliance review is still pending because "
                "document analysis has not completed."
            )

        if "AI_CONFIDENCE_LOW" in codes:
            return (
                "Compliance review is still pending because the "
                "COI evidence confidence is below the required threshold."
            )

        if "AI_VALIDATION_FAILED" in codes:
            return (
                "Compliance review is still pending because the "
                "uploaded COI could not be validated."
            )

        if "COI_EXPIRING_SOON" in codes:
            return (
                "Compliance review is pending because the "
                "Certificate of Insurance expires soon."
            )

        return "Compliance review is pending."

    return "Compliance status could not be explained."


def _actions_for_reasons(status, reasons):
    if status == READY:
        return ()

    actions = []

    for reason in reasons:
        action = _action_for_reason(reason)

        if action:
            actions.append(action)

    return tuple(
        sorted(
            actions,
            key=lambda action: _priority_rank(action.priority),
        )
    )


def _action_for_reason(reason):
    code = reason.get("code")
    severity = reason.get("severity")
    blocking = severity == "blocking"

    if code == "COI_MISSING":
        return ComplianceAction(
            title="Upload a valid Certificate of Insurance.",
            description=(
                "Add a current COI for this subcontractor before "
                "mobilization."
            ),
            priority=HIGH,
            blocking=True,
        )

    if code == "COI_EXPIRED":
        return ComplianceAction(
            title="Request a renewed insurance certificate.",
            description=(
                "Replace the expired COI with a current insurance "
                "certificate."
            ),
            priority=HIGH,
            blocking=True,
        )

    if code == "COI_EXPIRING_SOON":
        return ComplianceAction(
            title="Request a renewed insurance certificate.",
            description=(
                "Ask the subcontractor for an updated COI before "
                "the current certificate expires."
            ),
            priority=MEDIUM,
            blocking=False,
        )

    if code == "COI_DOCUMENT_PARTIAL":
        return ComplianceAction(
            title="Wait until document analysis completes.",
            description=(
                "Review this subcontractor again after Document "
                "Intelligence finishes processing the COI."
            ),
            priority=MEDIUM,
            blocking=False,
        )

    if code == "COI_DOCUMENT_UNREADABLE":
        return ComplianceAction(
            title="Replace unreadable document.",
            description=(
                "Upload a clearer COI so the document can be validated."
            ),
            priority=MEDIUM,
            blocking=False,
        )

    if code == "AI_CONFIDENCE_LOW":
        return ComplianceAction(
            title="Review COI evidence.",
            description=(
                "Verify the extracted COI information or upload a "
                "clearer document."
            ),
            priority=MEDIUM,
            blocking=False,
        )

    if code == "AI_VALIDATION_FAILED":
        return ComplianceAction(
            title="Review COI validation issues.",
            description=(
                "Check the uploaded COI and replace it if the required "
                "information cannot be validated."
            ),
            priority=MEDIUM,
            blocking=False,
        )

    if code == "COVERAGE_INSUFFICIENT":
        return ComplianceAction(
            title="Review insurance coverage.",
            description=(
                "Confirm that the subcontractor's coverage meets the "
                "project requirement."
            ),
            priority=HIGH,
            blocking=True,
        )

    if code == "REQUIREMENT_UNSUPPORTED":
        return ComplianceAction(
            title="Review compliance requirement.",
            description=(
                "This requirement is not supported by the current "
                "Compliance Profiles foundation."
            ),
            priority=HIGH if blocking else MEDIUM,
            blocking=blocking,
        )

    return ComplianceAction(
        title="Review compliance issue.",
        description=_safe_reason_message(reason),
        priority=HIGH if blocking else MEDIUM,
        blocking=blocking,
    )


def _dedupe_actions(actions):
    deduped = []
    seen = set()

    for action in actions:
        key = action.title

        if key in seen:
            continue

        seen.add(key)
        deduped.append(action)

    return tuple(deduped)


def _safe_reason_message(reason):
    message = reason.get("message")

    if message:
        return message

    return "Review this compliance issue."


def _priority_rank(priority):
    return {
        HIGH: 0,
        MEDIUM: 1,
        LOW: 2,
    }.get(
        priority,
        99,
    )


def _priority_for_status(status):
    if status == BLOCKED:
        return HIGH

    if status == PENDING:
        return MEDIUM

    return LOW


def _future_ai_summary_hook(summary, context):
    return summary


def _future_ai_actions_hook(actions, context):
    return actions
