import logging

from flask import (
    Blueprint,
    flash,
    redirect,
    request,
    url_for,
)

from flask_login import (
    current_user,
    login_required,
)

from app.extensions import db

from app.models import (
    Document,
    Project,
    Subcontractor,
)

from app.services.documents.storage import (
    delete_document_file,
    document_exists,
    get_document_response,
)


documents_bp = Blueprint(
    "documents",
    __name__,
)

logger = logging.getLogger(__name__)


# ==========================
# DOCUMENT AUTHORIZATION
# ==========================

def user_can_access_document(doc):

    if not doc:
        return False

    if not doc.sub_id and not doc.project_id:
        return False

    if doc.sub_id:

        sub = db.session.get(
            Subcontractor,
            doc.sub_id
        )

        if not sub or sub.user_id != current_user.id:
            return False

    if doc.project_id:

        project = db.session.get(
            Project,
            doc.project_id
        )

        if not project or project.user_id != current_user.id:
            return False

    return True


# ==========================
# VIEW DOCUMENT
# ==========================

@documents_bp.route("/document/<int:doc_id>")
@login_required
def view_document(doc_id):
    doc = db.session.get(
        Document,
        doc_id
    )

    if not doc:

        flash(
            "Document not found.",
            "danger"
        )

        return redirect(
            url_for("dashboard.dashboard")
        )

    if not user_can_access_document(doc):

        flash(
            "Unauthorized",
            "danger"
        )

        return redirect(
            url_for("dashboard.dashboard")
        )

    if not document_exists(doc):

        flash(
            "File not found.",
            "danger"
        )

        return redirect(
            url_for("dashboard.dashboard")
        )

    response = get_document_response(doc)

    if response is None:
        flash(
            "File not found.",
            "danger"
        )

        return redirect(
            url_for("dashboard.dashboard")
        )

    return response


# ==========================
# DELETE DOCUMENT
# ==========================

@documents_bp.route(
    "/delete_document/<int:doc_id>",
    methods=["POST"]
)
@login_required
def delete_document(doc_id):

    doc = db.session.get(
        Document,
        doc_id
    )

    if not doc:

        flash(
            "Document not found.",
            "danger"
        )

        return redirect(
            url_for("dashboard.dashboard")
        )

    if not user_can_access_document(doc):

        flash(
            "Unauthorized",
            "danger"
        )

        return redirect(
            url_for("dashboard.dashboard")
        )

    try:

        delete_document_file(doc)

        db.session.delete(doc)
        db.session.commit()

        flash(
            "Document deleted successfully.",
            "success"
        )

    except Exception:

        db.session.rollback()

        logger.exception(
            "Document delete failed for document_id=%s",
            doc.id,
        )

        flash(
            "Error deleting document.",
            "danger"
        )

    return redirect(
        request.referrer
        or url_for("dashboard.dashboard")
    )


# ==========================
# DOWNLOAD DOCUMENT
# ==========================

@documents_bp.route("/download_document/<int:doc_id>")
@login_required
def download_document(doc_id):

    doc = Document.query.get_or_404(
        doc_id
    )

    if not user_can_access_document(doc):

        flash(
            "Unauthorized",
            "danger"
        )

        return redirect(
            url_for("dashboard.dashboard")
        )

    if not document_exists(doc):

        flash(
            "File not found.",
            "danger"
        )

        return redirect(
            url_for("dashboard.dashboard")
        )

    response = get_document_response(
        doc,
        as_attachment=True,
    )

    if response is None:
        flash(
            "File not found.",
            "danger"
        )

        return redirect(
            url_for("dashboard.dashboard")
        )

    return response
