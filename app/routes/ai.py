from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    url_for,
)

from flask_login import (
    current_user,
    login_required,
)

from app.models import (
    Document,
    Project,
    Subcontractor,
)

from app.services.document_analysis_service import (
    analyze_and_save_document,
)


ai_bp = Blueprint(
    "ai",
    __name__,
)


def user_can_access_document(doc):

    if not doc:
        return False

    if doc.sub_id:

        sub = Subcontractor.query.get(doc.sub_id)

        if not sub or sub.user_id != current_user.id:
            return False

    if doc.project_id:

        project = Project.query.get(doc.project_id)

        if not project or project.user_id != current_user.id:
            return False

    return True


@ai_bp.route("/documents/<int:doc_id>/analyze")
@login_required
def analyze_document(doc_id):

    doc = Document.query.get_or_404(doc_id)

    if not user_can_access_document(doc):

        flash(
            "Unauthorized document access.",
            "danger"
        )

        return redirect(
            url_for("dashboard.dashboard")
        )

    analysis = analyze_and_save_document(
        doc_id
    )

    if not analysis["success"]:

        flash(
            f"AI analysis failed: {analysis['error']}",
            "danger"
        )

        return redirect(
            url_for("dashboard.dashboard")
        )

    return render_template(
        "ai_result.html",
        doc=analysis["document"],
        result=analysis["result"],
    )