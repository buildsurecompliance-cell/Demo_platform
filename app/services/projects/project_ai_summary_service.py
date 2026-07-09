from app.models import (
    Document,
    Project,
    ProjectSubcontractor,
)


def get_project_ai_summary(project_id):

    project = Project.query.get(project_id)

    if not project:
        return None

    links = (
        ProjectSubcontractor.query
        .filter_by(project_id=project_id)
        .all()
    )

    sub_ids = [
        link.subcontractor_id
        for link in links
    ]

    documents = (
        Document.query
        .filter(
            Document.sub_id.in_(sub_ids)
        )
        .all()
    )

    ready = 0
    pending = 0
    blocked = 0

    total_score = 0
    score_count = 0

    critical_issues = []

    for doc in documents:

        if doc.ai_status != "analyzed":
            continue

        compliance = (
            doc.ai_compliance_result
            or {}
        )

        status = compliance.get("status")

        score = compliance.get("score")

        if score is not None:
            total_score += score
            score_count += 1

        if status == "Blocked":

            blocked += 1

        elif status == "Pending Renewal":

            pending += 1

        else:

            ready += 1

        issues = compliance.get(
            "issues",
            []
        )

        if issues:

            critical_issues.append({

                "document_id": doc.id,

                "subcontractor": (
                    doc.sub.name
                    if doc.sub
                    else "-"
                ),

                "document_type": doc.document_type,

                "issues": issues,

            })

    average_score = 100

    if score_count:

        average_score = round(
            total_score / score_count
        )

    if blocked:

        project_health = "High"

        mobilization = "Blocked"

    elif pending:

        project_health = "Medium"

        mobilization = "Pending"

    else:

        project_health = "Low"

        mobilization = "Ready"

    revenue_at_risk = 0

    if mobilization != "Ready":

        revenue_at_risk = (
            project.contract_value or 0
        )

    return {

        "score": average_score,

        "ready": ready,

        "pending": pending,

        "blocked": blocked,

        "risk": project_health,

        "mobilization": mobilization,

        "revenue_at_risk": revenue_at_risk,

        "critical_issues": critical_issues,

    }