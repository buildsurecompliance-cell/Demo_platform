from flask import (
    abort,
    Blueprint,
    render_template,
    request,
)

from flask_login import (
    login_required,
    current_user,
)

from app.decorators import subscription_required
from app.models import (
    Project,
    ProjectSubcontractor,
    Subcontractor,
)
from sqlalchemy.orm import selectinload
from app.services.organizations import (
    get_current_organization,
    scoped_project_query,
    scoped_subcontractor_query,
)
from app.services.readiness_service import (
    BLOCKED,
    READY,
    calculate_readiness,
)

dashboard_bp = Blueprint(
    "dashboard",
    __name__,
)


def _money_short(value):
    amount = float(value or 0)
    abs_amount = abs(amount)

    if abs_amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.1f}B"

    if abs_amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"

    if abs_amount >= 1_000:
        return f"${amount / 1_000:.1f}K"

    return f"${amount:,.0f}"


def _money_full(value):
    return f"${float(value or 0):,.0f}"


def _coverage_label(value):
    if not value:
        return "No minimum"

    amount = int(value)
    presets = {
        1_000_000: "$1M",
        2_000_000: "$2M",
        5_000_000: "$5M",
    }

    return presets.get(amount, _money_full(amount))


def _subcontractor_coverage_label(sub):
    coverage_values = []

    for document in sub.documents:
        extracted = document.ai_extracted_data or {}
        coverage = (
            extracted.get("general_liability_limit")
            or extracted.get("coverage")
        )

        if not coverage:
            continue

        try:
            coverage_values.append(int(float(coverage)))
        except (TypeError, ValueError):
            continue

    if not coverage_values:
        return "Not available"

    return _coverage_label(max(coverage_values))


def _readiness_label(status):
    if status == "No Subcontractors Assigned":
        return "NO SUBCONTRACTORS"

    if status == "Ready to Mobilize":
        return "READY"

    if status == "Pending Compliance":
        return "NEEDS ATTENTION"

    return "BLOCKED"


def _dashboard_readiness_for_project(project):
    if not project.subs:
        return "NO SUBCONTRACTORS"

    saw_checking = False
    saw_attention = False

    for link in project.subs:
        readiness = calculate_readiness(link)

        if readiness["status"] == READY:
            continue

        if _is_processing_readiness(readiness):
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


def _is_processing_readiness(readiness):
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


def _project_row(project, status=None):
    readiness = status or _dashboard_readiness_for_project(project)
    compliance_score = project.compliance_score

    return {
        "project": project,
        "name": project.name,
        "contract_value": _money_short(project.contract_value),
        "contract_value_full": _money_full(project.contract_value),
        "required_coverage": _coverage_label(project.required_coverage),
        "schedule": _schedule_label(project),
        "subcontractor_count": len(project.subs),
        "compliance_score": compliance_score,
        "risk": _risk_from_score(compliance_score),
        "readiness": readiness,
    }


def _risk_from_score(score):
    if score is None:
        return "No Subcontractors"

    if score == 100:
        return "Low"

    if score >= 70:
        return "Medium"

    return "High"


def _schedule_label(project):
    if project.days_remaining is None:
        return "Not scheduled"

    if project.days_remaining == 0:
        return "Expired"

    return f"{project.days_remaining} days left"


def _subcontractor_row(sub):
    status = _sub_status_label(sub)

    return {
        "subcontractor": sub,
        "company": sub.name,
        "trade": sub.role or "Not specified",
        "expiration": (
            sub.coi_expiration.strftime("%m/%d/%Y")
            if sub.coi_expiration
            else "Not available"
        ),
        "coverage": _subcontractor_coverage_label(sub),
        "status": status,
        "project_count": len(sub.projects),
        "document_count": len(sub.documents),
        "last_reminder": sub.last_reminder_sent,
    }


def _sub_status_label(sub):
    if not sub.coi_expiration:
        return "MISSING"

    if sub.computed_status == "expired":
        return "EXPIRED"

    if sub.computed_status == "at_risk":
        return "AT RISK"

    return "COMPLIANT"


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

            if _is_processing_readiness(readiness):
                continue

            reason = (
                readiness["reasons"][0]["message"]
                if readiness.get("reasons")
                else "Compliance needs review."
            )
            items.append(
                {
                    "subcontractor": link.subcontractor,
                    "project": project,
                    "status": "BLOCKED" if status == BLOCKED else "NEEDS ATTENTION",
                    "reason": reason,
                }
            )

    return items


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

    # =========================
    # FILTERS
    # =========================

    status_filter = request.args.get("status")

    search = request.args.get(
        "search",
        ""
    ).strip()

    project_search = request.args.get(
        "project_search",
        ""
    ).strip()

    contract_status = request.args.get(
        "contract_status"
    )

    risk_level = request.args.get(
        "risk_level"
    )

    # =========================
    # SUBCONTRACTORS
    # =========================

    query = scoped_subcontractor_query().options(
        selectinload(Subcontractor.documents),
        selectinload(Subcontractor.projects),
    )

    if search:

        query = query.filter(
            Subcontractor.name.ilike(
                f"%{search}%"
            )
        )

    subs = query.all()

    # =========================
    # KPI COUNTERS
    # =========================

    expired_count = 0
    at_risk_count = 0
    compliant_count = 0

    for sub in subs:

        status = sub.computed_status

        if status == "expired":

            expired_count += 1

        elif status == "at_risk":

            at_risk_count += 1

        elif status == "compliant":

            compliant_count += 1

    # =========================
    # STATUS FILTER
    # =========================

    if status_filter:

        subs = [

            sub

            for sub in subs

            if sub.computed_status == status_filter

        ]

    # =========================
    # RISK PRIORITY
    # =========================

    def risk_priority(sub):

        status = sub.computed_status

        days_left = sub.days_left

        if status == "expired":
            return (-2, 0)

        if status == "at_risk":
            return (-1, days_left or 0)

        return (0, 999)

    top_risk = sorted(
        subs,
        key=risk_priority
    )

    # =========================
    # PROJECTS
    # =========================

    projects_query = scoped_project_query().options(
        selectinload(Project.subs)
        .selectinload(ProjectSubcontractor.subcontractor),
    )

    if project_search:

        projects_query = projects_query.filter(
            Project.name.ilike(
                f"%{project_search}%"
            )
        )

    projects = projects_query.order_by(
        Project.id.desc()
    ).all()

    filtered_projects = []

    for project in projects:

        if contract_status:

            days = project.days_remaining

            if (
                contract_status == "active"
                and (
                    days is None
                    or days <= 30
                )
            ):
                continue

            if (
                contract_status == "expiring"
                and (
                    days is None
                    or days > 30
                    or days <= 0
                )
            ):
                continue

            if (
                contract_status == "expired"
                and (
                    days is None
                    or days > 0
                )
            ):
                continue

        filtered_projects.append(
            project
        )

    projects = filtered_projects

    # =========================
    # PORTFOLIO METRICS
    # =========================

    ready_projects = 0
    checking_projects = 0
    pending_projects = 0
    blocked_projects = 0
    unassigned_projects = 0
    project_rows = []

    for project in projects:

        readiness = _dashboard_readiness_for_project(project)
        project_rows.append(
            _project_row(
                project,
                readiness,
            )
        )

        if readiness == "READY":
            ready_projects += 1
        elif readiness == "CHECKING":
            checking_projects += 1
        elif readiness == "NEEDS ATTENTION":
            pending_projects += 1
        elif readiness == "NO SUBCONTRACTORS":
            unassigned_projects += 1
        else:
            blocked_projects += 1

    # =========================
    # TEMPLATE
    # =========================

    return render_template(
        "dashboard.html",
        subs=subs,
        sub_rows=[
            _subcontractor_row(sub)
            for sub in subs
        ],
        top_risk=top_risk,
        projects=projects,
        project_rows=project_rows,
        active_projects=len(projects),
        expired_count=expired_count,
        at_risk_count=at_risk_count,
        compliant_count=compliant_count,
        ready_projects=ready_projects,
        checking_projects=checking_projects,
        pending_projects=pending_projects,
        blocked_projects=blocked_projects,
        unassigned_projects=unassigned_projects,
        needs_attention=_readiness_attention_items(projects),
    )
