import logging

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.decorators import subscription_required
from app.extensions import db
from app.models import (
    Document,
    Project,
    Subcontractor,
)
from app.routes.subcontractors import allowed_file
from app.security import rate_limited
from app.services.document_analysis_service import analyze_and_save_document
from app.services.document_requests import (
    DocumentRequestError,
    complete_document_request,
    create_or_resend_coi_request,
    get_valid_document_request,
)
from app.services.documents.storage import (
    cleanup_saved_document,
    save_document_file,
)
from app.services.documents.types import SUBCONTRACTOR_DOCUMENT_TYPE
from app.services.organizations import (
    get_current_organization,
    project_scope_filter,
    subcontractor_scope_filter,
)


document_requests_bp = Blueprint(
    "document_requests",
    __name__,
)

logger = logging.getLogger(__name__)


@document_requests_bp.route(
    "/project/<int:project_id>/subcontractor/<int:subcontractor_id>/request-coi",
    methods=["POST"],
)
@login_required
@subscription_required
def request_coi(project_id, subcontractor_id):
    organization = get_current_organization()

    if not organization:
        return _gc_failure("Unable to send COI request.")

    project = (
        Project.query
        .filter_by(id=project_id)
        .filter(project_scope_filter(Project))
        .first_or_404()
    )
    subcontractor = (
        Subcontractor.query
        .filter_by(id=subcontractor_id)
        .filter(subcontractor_scope_filter(Subcontractor))
        .first_or_404()
    )

    try:
        delivery = create_or_resend_coi_request(
            organization=organization,
            project=project,
            subcontractor=subcontractor,
            created_by_user_id=current_user.id,
        )
    except DocumentRequestError as error:
        flash(str(error), "warning")
        return redirect(
            url_for(
                "projects.view_project",
                project_id=project.id,
            )
        )

    if delivery.sent:
        flash(
            f"COI request sent to {subcontractor.email}",
            "success",
        )
    else:
        flash(
            "COI request was created, but email could not be sent.",
            "warning",
        )

    return redirect(
        url_for(
            "projects.view_project",
            project_id=project.id,
        )
    )


@document_requests_bp.route(
    "/document-request/<token>",
    methods=["GET", "POST"],
)
@rate_limited("DOCUMENT_REQUEST_UPLOAD_RATE_LIMIT")
def public_document_request(token):
    document_request = get_valid_document_request(token)

    if not document_request:
        return render_template(
            "document_request_invalid.html",
        ), 404

    if request.method == "GET":
        return render_template(
            "document_request_upload.html",
            document_request=document_request,
        )

    file = request.files.get("file")

    if not file or file.filename == "":
        flash("Choose a COI file to upload.", "danger")
        return render_template(
            "document_request_upload.html",
            document_request=document_request,
        ), 400

    if not allowed_file(file.filename):
        flash("Upload a PDF, JPG, JPEG, or PNG file.", "danger")
        return render_template(
            "document_request_upload.html",
            document_request=document_request,
        ), 400

    saved_storage_key = None

    try:
        original_name = file.filename
        saved_storage_key = save_document_file(
            file,
            original_name,
            sub_id=document_request.subcontractor_id,
        )

        version = _next_subcontractor_document_version(
            document_request.subcontractor_id,
            SUBCONTRACTOR_DOCUMENT_TYPE,
        )
        document = Document(
            filename=saved_storage_key,
            original_name=original_name,
            document_type=SUBCONTRACTOR_DOCUMENT_TYPE,
            version=version,
            sub_id=document_request.subcontractor_id,
            uploaded_by=document_request.created_by_user_id,
        )
        db.session.add(document)
        db.session.flush()
        complete_document_request(document_request, document)
        db.session.commit()

    except Exception:
        db.session.rollback()

        if saved_storage_key:
            cleanup_saved_document(saved_storage_key)

        logger.exception(
            "Document request upload failed request_id=%s",
            document_request.id,
        )
        flash("We could not upload that file.", "danger")
        return render_template(
            "document_request_upload.html",
            document_request=document_request,
        ), 500

    _analyze_uploaded_document(document.id)

    return render_template(
        "document_request_complete.html",
        document_request=document_request,
    )


def _gc_failure(message):
    flash(message, "danger")
    return redirect(url_for("dashboard.dashboard"))


def _next_subcontractor_document_version(subcontractor_id, document_type):
    latest = (
        Document.query
        .filter_by(
            sub_id=subcontractor_id,
            document_type=document_type,
        )
        .order_by(Document.version.desc())
        .first()
    )

    return (latest.version + 1) if latest else 1


def _analyze_uploaded_document(document_id):
    try:
        analysis = analyze_and_save_document(document_id)
    except Exception as error:
        db.session.rollback()
        document = db.session.get(Document, document_id)

        if document:
            document.ai_status = "failed"
            document.ai_error = "Document analysis failed."
            db.session.commit()

        logger.error(
            "Document request automatic analysis failed document_id=%s error_type=%s",
            document_id,
            error.__class__.__name__,
        )
        return False

    return bool(analysis.get("success"))
