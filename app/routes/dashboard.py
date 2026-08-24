from flask import (
    abort,
    Blueprint,
    render_template,
)

from flask_login import (
    login_required,
)

from app.decorators import subscription_required
from app.models import (
    DOCUMENT_REQUEST_PENDING,
    DocumentRequest,
    Project,
    ProjectSubcontractor,
    Subcontractor,
)
from datetime import datetime, timezone
from sqlalchemy.orm import selectinload
from app.services.organizations import (
    get_current_organization,
    scoped_project_query,
)
from app.services.readiness_service import (
    READY,
    calculate_readiness,
)
from app.services.dashboard.project_readiness import (
    dashboard_readiness_for_project,
    is_processing_readiness,
)
from app.services.compliance_evidence_service import (
    collect_coi_evidence,
)
from app.services.documents.types import SUBCONTRACTOR_DOCUMENT_TYPE

dashboard_bp = Blueprint(
    "dashboard",
    __name__,
)


def _money_short(value):
    amount = float(value or 0)
    abs_amount = abs(amount)

    if abs_amount >= 1_000_000_000:
        formatted = amount / 1_000_000_000
        suffix = "B"
    elif abs_amount >= 1_000_000:
        formatted = amount / 1_000_000
        suffix = "M"
    elif abs_amount >= 1_000:
        formatted = amount / 1_000
        suffix = "K"
    else:
        return f"${amount:,.0f}"

    if formatted.is_integer():
        return f"${int(formatted)}{suffix}"

    return f"${formatted:.1f}{suffix}"


def _coverage_label(value):
    if not value:
        return "No minimum"

    amount = int(value)
    return _money_short(amount)


def _short_date_label(value):
    if not value:
        return "Not available"

    if isinstance(value, datetime):
        value = value.date()

    return value.strftime("%b %d, %Y").replace(" 0", " ")


def _readiness_attention_items(projects):
    items = []

    for project in projects:
        for link in project.subs:
            if not link.subcontractor:
                continue

            readiness = calculate_readiness(link)
            status = readiness["status"]

            if status == READY:
                continue

            if is_processing_readiness(readiness):
                continue

            latest_request = _latest_document_request(link)

            if _is_pending_request_valid(latest_request):
                continue

            item = _attention_item(
                project,
                link,
                readiness,
            )

            if item:
                items.append(item)

    return sorted(
        items,
        key=lambda item: (
            item["sort"],
            item["project"].name.lower(),
            item["subcontractor"].name.lower(),
        ),
    )


def _latest_document_request(project_subcontractor):
    return (
        DocumentRequest.query
        .filter_by(
            project_id=project_subcontractor.project_id,
            subcontractor_id=project_subcontractor.subcontractor_id,
            document_type=SUBCONTRACTOR_DOCUMENT_TYPE,
        )
        .order_by(DocumentRequest.created_at.desc())
        .first()
    )


def _is_pending_request_valid(document_request):
    if (
        not document_request
        or document_request.status != DOCUMENT_REQUEST_PENDING
    ):
        return False

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return not document_request.expires_at or document_request.expires_at >= now


def _attention_item(project, link, readiness):
    reason_codes = [
        reason.get("code")
        for reason in readiness.get("reasons", [])
    ]
    evidence = _validated_coi_evidence(link.subcontractor)
    issue = _issue_label(
        reason_codes,
        readiness,
        project,
        link.subcontractor,
        evidence,
    )
    action = _action_for_item(
        reason_codes,
        project,
        link.subcontractor,
    )

    if not action:
        return None

    return {
        "subcontractor": link.subcontractor,
        "project": project,
        "issue": issue,
        "action": action,
        "sort": _attention_sort(reason_codes),
    }


def _validated_coi_evidence(subcontractor):
    for evidence in collect_coi_evidence(subcontractor):
        if evidence.validated:
            return evidence

    return None


def _issue_label(reason_codes, readiness, project, subcontractor, evidence):
    if "COI_MISSING" in reason_codes:
        return "COI missing"

    if "COI_EXPIRED" in reason_codes:
        expiration = _coi_expiration_for_issue(subcontractor, evidence)
        if expiration:
            return f"COI expired {_short_date_label(expiration)}"

        return "COI expired"

    if "COVERAGE_INSUFFICIENT" in reason_codes:
        current = _coi_coverage_for_issue(evidence)
        required = getattr(project, "required_coverage", None)
        if current is not None and required:
            return (
                f"GL {_coverage_label(current)} / "
                f"Required {_coverage_label(required)}"
            )

        return "GL coverage below requirement"

    if (
        "COI_DOCUMENT_UNREADABLE" in reason_codes
        or "COI_LOW_CONFIDENCE" in reason_codes
        or "COI_VALIDATOR_FAILED" in reason_codes
    ):
        return "COI analysis failed"

    if "COI_DOCUMENT_PARTIAL" in reason_codes:
        return "COI analysis incomplete"

    if "COVERAGE_EVIDENCE_MISSING" in reason_codes:
        return "Validated GL coverage missing"

    if "COI_EXPIRING_SOON" in reason_codes:
        expiration = _coi_expiration_for_issue(subcontractor, evidence)
        if expiration:
            return f"COI expires {_short_date_label(expiration)}"

        return "COI expiring soon"

    if readiness.get("reasons"):
        return readiness["reasons"][0].get(
            "message",
            "Compliance needs review.",
        )

    return "Compliance needs review."


def _coi_expiration_for_issue(subcontractor, evidence):
    if evidence:
        expiration = evidence.value.get("expiration_date")
        if expiration:
            return expiration

    return getattr(subcontractor, "coi_expiration", None)


def _coi_coverage_for_issue(evidence):
    if not evidence:
        return None

    return evidence.value.get("coverage")


def _action_for_item(reason_codes, project, subcontractor):
    if (
        "COI_DOCUMENT_UNREADABLE" in reason_codes
        or "COI_DOCUMENT_PARTIAL" in reason_codes
        or "COI_LOW_CONFIDENCE" in reason_codes
        or "COI_VALIDATOR_FAILED" in reason_codes
    ):
        return {
            "label": "Review Documents",
            "kind": "link",
            "endpoint": "subcontractors.view_sub_documents",
            "params": {"sub_id": subcontractor.id},
        }

    if not (subcontractor.email or "").strip():
        return {
            "label": "Add Email",
            "kind": "link",
            "endpoint": "subcontractors.edit_sub",
            "params": {"id": subcontractor.id},
        }

    if "COVERAGE_INSUFFICIENT" in reason_codes:
        return {
            "label": "Request Corrected COI",
            "kind": "post",
            "endpoint": "document_requests.request_coi",
            "params": {
                "project_id": project.id,
                "subcontractor_id": subcontractor.id,
            },
        }

    return {
        "label": "Request COI",
        "kind": "post",
        "endpoint": "document_requests.request_coi",
        "params": {
            "project_id": project.id,
            "subcontractor_id": subcontractor.id,
        },
    }


def _attention_sort(reason_codes):
    if "COI_MISSING" in reason_codes or "COI_EXPIRED" in reason_codes:
        return 0

    if "COVERAGE_INSUFFICIENT" in reason_codes:
        return 1

    if (
        "COI_DOCUMENT_UNREADABLE" in reason_codes
        or "COI_DOCUMENT_PARTIAL" in reason_codes
        or "COI_LOW_CONFIDENCE" in reason_codes
        or "COI_VALIDATOR_FAILED" in reason_codes
    ):
        return 2

    return 3


# ==========================
# DASHBOARD
# ==========================

@dashboard_bp.route("/dashboard")
@login_required
@subscription_required
def dashboard():
    organization = get_current_organization()

    if not organization:
        abort(403)

    projects_query = scoped_project_query().options(
        selectinload(Project.subs)
        .selectinload(ProjectSubcontractor.subcontractor),
        selectinload(Project.subs)
        .selectinload(ProjectSubcontractor.subcontractor)
        .selectinload(Subcontractor.documents),
    )

    projects = projects_query.order_by(
        Project.id.desc()
    ).all()

    ready_projects = 0
    checking_projects = 0
    blocked_projects = 0
    unassigned_projects = 0

    for project in projects:

        readiness = dashboard_readiness_for_project(project)

        if readiness == "READY":
            ready_projects += 1
        elif readiness == "CHECKING":
            checking_projects += 1
        elif readiness == "NO SUBCONTRACTORS":
            unassigned_projects += 1
        elif readiness == "BLOCKED":
            blocked_projects += 1

    needs_attention = _readiness_attention_items(projects)

    return render_template(
        "dashboard.html",
        active_projects=len(projects),
        ready_projects=ready_projects,
        checking_projects=checking_projects,
        blocked_projects=blocked_projects,
        unassigned_projects=unassigned_projects,
        needs_attention=needs_attention,
        needs_attention_count=len(needs_attention),
    )
