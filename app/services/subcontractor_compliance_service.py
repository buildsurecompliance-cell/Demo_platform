from app.extensions import db

from app.models import (
    Document,
    Subcontractor,
)


def get_subcontractor_ai_compliance_summary(sub_id):

    sub = db.session.get(Subcontractor, sub_id)

    if not sub:
        return None

    docs = Document.query.filter_by(sub_id=sub_id).all()

    analyzed = [
        d for d in docs
        if d.ai_status == "analyzed"
        and d.ai_compliance_result
    ]

    if not analyzed:
        return {
            "status": "Not Analyzed",
            "risk_level": "Unknown",
            "score": None,
            "documents_analyzed": 0,
        }

    statuses = [
        d.ai_compliance_result.get("status")
        for d in analyzed
    ]

    risks = [
        d.ai_compliance_result.get("risk_level")
        for d in analyzed
    ]

    scores = [
        d.ai_compliance_result.get("score")
        for d in analyzed
        if d.ai_compliance_result.get("score") is not None
    ]

    if "Blocked" in statuses:
        status = "Blocked"
    elif "Pending Renewal" in statuses:
        status = "Pending Renewal"
    elif "Ready - Renewal Required" in statuses:
        status = "Ready - Renewal Required"
    elif "Pending" in statuses:
        status = "Pending"
    else:
        status = "Ready"

    if "High" in risks:
        risk_level = "High"
    elif "Medium" in risks:
        risk_level = "Medium"
    else:
        risk_level = "Low"

    avg_score = int(sum(scores) / len(scores)) if scores else None

    return {
        "status": status,
        "risk_level": risk_level,
        "score": avg_score,
        "documents_analyzed": len(analyzed),
    }


def update_subcontractor_compliance(sub_id):

    return get_subcontractor_ai_compliance_summary(sub_id)