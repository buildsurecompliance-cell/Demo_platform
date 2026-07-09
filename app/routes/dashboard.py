from flask import (
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
    Subcontractor,
)

dashboard_bp = Blueprint(
    "dashboard",
    __name__,
)


# ==========================
# DASHBOARD
# ==========================

@dashboard_bp.route("/dashboard")
@login_required
def dashboard():

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

    query = Subcontractor.query.filter_by(
        user_id=current_user.id
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

    projects_query = Project.query.filter_by(
        user_id=current_user.id
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

    for project in projects:

        contract_value = (
            project.contract_value or 0
        )

        total_portfolio += contract_value

        if (
            project.mobilization_status
            != "Ready to Mobilize"
        ):

            revenue_at_risk += contract_value

    # =========================
    # AI DASHBOARD
    # =========================

    documents = (
        Document.query
        .join(Subcontractor)
        .filter(
            Subcontractor.user_id == current_user.id
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

    # =========================
    # TEMPLATE
    # =========================

    return render_template(
        "dashboard.html",
        subs=subs,
        top_risk=top_risk,
        projects=projects,
        expired_count=expired_count,
        at_risk_count=at_risk_count,
        compliant_count=compliant_count,
        total_portfolio=total_portfolio,
        revenue_at_risk=revenue_at_risk,
        documents_analyzed=documents_analyzed,
        blocked_documents=blocked_documents,
        pending_documents=pending_documents,
        ready_documents=ready_documents,
        average_ai_score=average_ai_score,
    )