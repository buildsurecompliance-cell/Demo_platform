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

from app.models import (
    Document,
    Project,
    ProjectSubcontractor,
    Subcontractor,
)
from sqlalchemy.orm import selectinload
from app.services.organizations import (
    get_current_organization,
    project_scope_filter,
    scoped_project_query,
    scoped_subcontractor_query,
    subcontractor_scope_filter,
)
from app.services.plan_capacity import (
    get_organization_plan,
    get_organization_usage,
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
    if status == "Ready to Mobilize":
        return "READY"

    if status == "Pending Compliance":
        return "PENDING"

    return "BLOCKED"


def _capacity_item(current, limit):
    if limit is None:
        return {
            "text": f"{current} / Unlimited",
            "percent": None,
            "warning": False,
        }

    percent = int((current / limit) * 100) if limit else 0

    return {
        "text": f"{current} / {limit}",
        "percent": min(percent, 100),
        "warning": percent >= 80,
    }


def _project_row(project, status=None):
    status = status or project.mobilization_status
    readiness = _readiness_label(status)
    compliance_score = project.compliance_score or 0

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


# ==========================
# DASHBOARD
# ==========================

@dashboard_bp.route("/dashboard")
@login_required
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

        else:

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

        if (
            risk_level
            and project.risk_level != risk_level
        ):
            continue

        filtered_projects.append(
            project
        )

    projects = filtered_projects

    # =========================
    # PORTFOLIO METRICS
    # =========================

    total_portfolio = 0

    revenue_at_risk = 0
    ready_projects = 0
    pending_projects = 0
    blocked_projects = 0
    project_rows = []

    for project in projects:

        contract_value = (
            project.contract_value or 0
        )

        total_portfolio += contract_value

        mobilization_status = project.mobilization_status
        readiness = _readiness_label(
            mobilization_status
        )
        project_rows.append(
            _project_row(
                project,
                mobilization_status,
            )
        )

        if readiness == "READY":
            ready_projects += 1
        elif readiness == "PENDING":
            pending_projects += 1
        else:
            blocked_projects += 1

        if readiness != "READY":
            revenue_at_risk += contract_value

    # =========================
    # AI DASHBOARD
    # =========================

    documents = (
        Document.query
        .join(Subcontractor)
        .filter(
            subcontractor_scope_filter(Subcontractor)
        )
        .all()
    )

    documents_analyzed = 0
    blocked_documents = 0
    pending_documents = 0
    ready_documents = 0

    total_score = 0
    score_count = 0

    for doc in documents:

        if doc.ai_status != "analyzed":
            continue

        documents_analyzed += 1

        result = doc.ai_compliance_result or {}

        status = result.get("status")
        score = result.get("score")

        if status == "Blocked":
            blocked_documents += 1

        elif status == "Pending Renewal":
            pending_documents += 1

        else:
            ready_documents += 1

        if score is not None:
            total_score += score
            score_count += 1

    average_ai_score = 0

    if score_count:
        average_ai_score = round(
            total_score / score_count
        )

    capacity_plan = get_organization_plan(organization)
    capacity_usage = get_organization_usage(organization)
    capacity_view = {
        "projects": _capacity_item(
            capacity_usage.project_count,
            capacity_plan.max_projects,
        ),
        "subcontractors": _capacity_item(
            capacity_usage.subcontractor_count,
            capacity_plan.max_subcontractors,
        ),
    }

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
        pending_projects=pending_projects,
        blocked_projects=blocked_projects,
        total_portfolio=total_portfolio,
        total_portfolio_label=_money_short(total_portfolio),
        total_portfolio_full=_money_full(total_portfolio),
        revenue_at_risk=revenue_at_risk,
        revenue_at_risk_label=_money_short(revenue_at_risk),
        revenue_at_risk_full=_money_full(revenue_at_risk),
        documents_analyzed=documents_analyzed,
        blocked_documents=blocked_documents,
        pending_documents=pending_documents,
        ready_documents=ready_documents,
        average_ai_score=average_ai_score,
        capacity_plan=capacity_plan,
        capacity_usage=capacity_usage,
        capacity_view=capacity_view,
    )
