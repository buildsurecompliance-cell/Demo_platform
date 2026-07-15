import logging

from flask import (
    abort,
    Blueprint,
    flash,
    redirect,
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
from app.extensions import db

from app.services.document_analysis_service import (
    analyze_and_save_document,
)
from app.services.organizations import (
    project_scope_filter,
    subcontractor_scope_filter,
)


ai_bp = Blueprint(
    "ai",
    __name__,
)
logger = logging.getLogger(__name__)


def user_can_access_document(doc):

    if not doc:
        return False

    if doc.sub_id:

        sub = db.session.get(
            Subcontractor,
            doc.sub_id,
        )

        if (
            not sub
            or not Subcontractor.query
            .filter(
                Subcontractor.id == sub.id,
                subcontractor_scope_filter(Subcontractor),
            )
            .first()
        ):
            return False

    if doc.project_id:

        project = db.session.get(
            Project,
            doc.project_id,
        )

        if (
            not project
            or not Project.query
            .filter(
                Project.id == project.id,
                project_scope_filter(Project),
            )
            .first()
        ):
            return False

    return True


def _analysis_redirect_target(doc):
    if doc and doc.sub_id:
        return redirect(
            url_for(
                "subcontractors.view_sub_documents",
                sub_id=doc.sub_id,
            )
        )

    if doc and doc.project_id:
        return redirect(
            url_for(
                "projects.view_project",
                project_id=doc.project_id,
            )
        )

    return redirect(
        url_for("dashboard.dashboard")
    )


@ai_bp.route(
    "/documents/<int:doc_id>/analyze",
    methods=["POST"],
)
@login_required
def analyze_document(doc_id):

    doc = db.session.get(
        Document,
        doc_id,
    )

    if not doc:
        abort(404)

    if not user_can_access_document(doc):
        logger.warning(
            "Unauthorized document analysis attempt document_id=%s user_id=%s",
            doc.id,
            current_user.id,
        )
        abort(404)

    if doc.ai_status == "analyzing":
        flash(
            "Document analysis is already in progress.",
            "warning",
        )

        return _analysis_redirect_target(doc)

    doc.ai_status = "analyzing"
    db.session.commit()

    try:
        analysis = analyze_and_save_document(
            doc_id
        )
    except Exception as error:
        db.session.rollback()

        failed_doc = db.session.get(
            Document,
            doc_id,
        )

        if failed_doc:
            failed_doc.ai_status = "failed"
            failed_doc.ai_error = "Document analysis failed."
            db.session.commit()

        logger.error(
            "Manual document analysis failed document_id=%s error_type=%s",
            doc_id,
            error.__class__.__name__,
        )
        flash(
            "Document analysis failed. Please try again.",
            "danger",
        )

        return _analysis_redirect_target(failed_doc or doc)

    if not analysis["success"]:
        doc.ai_status = "failed"
        doc.ai_error = "Document analysis failed."
        db.session.commit()

        flash(
            "Document analysis failed. Please try again.",
            "danger",
        )

        return _analysis_redirect_target(doc)

    flash(
        "Document analysis completed.",
        "success",
    )

    return _analysis_redirect_target(
        analysis["document"]
    )
