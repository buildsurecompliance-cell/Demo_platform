import uuid

from datetime import datetime
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import (
    abort,
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from flask_login import (
    current_user,
    login_required,
)

from werkzeug.utils import secure_filename

from app.extensions import db

from app.models import (
    Document,
    Project,
    ProjectSubcontractor,
    Subcontractor,
)

from app.services.document_analysis_service import (
    analyze_and_save_document,
)

from app.services.documents.storage import (
    cleanup_saved_document,
    delete_document_file,
    save_document_file,
)
from app.services.organizations import (
    get_current_organization,
    project_scope_filter,
    scoped_project_query,
    scoped_subcontractor_query,
    subcontractor_scope_filter,
)
from app.services.plan_capacity import (
    PlanCapacityError,
    get_organization_usage,
    require_subcontractor_capacity,
)


subcontractors_bp = Blueprint(
    "subcontractors",
    __name__,
)

logger = logging.getLogger(__name__)


def allowed_file(filename):
    safe_name = secure_filename(filename or "")

    if not safe_name or "." not in safe_name:
        logger.warning("Upload rejected for invalid filename")
        return False

    allowed_extensions = current_app.config.get(
        "ALLOWED_EXTENSIONS",
        {"pdf", "jpg", "jpeg", "png"}
    )
    dangerous_extensions = current_app.config.get(
        "DANGEROUS_UPLOAD_EXTENSIONS",
        {
            "bat",
            "cmd",
            "com",
            "exe",
            "html",
            "htm",
            "js",
            "php",
            "ps1",
            "sh",
            "svg",
            "vbs",
        },
    )
    parts = [
        part.lower()
        for part in safe_name.rsplit(".", maxsplit=10)
    ]
    extension = parts[-1]

    if extension not in allowed_extensions:
        logger.warning(
            "Upload rejected for extension extension=%s",
            extension,
        )
        return False

    if any(part in dangerous_extensions for part in parts[:-1]):
        logger.warning(
            "Upload rejected for dangerous double extension extension=%s",
            extension,
        )
        return False

    return True


def _selected_owned_project_ids():
    selected_ids = []

    for raw_id in request.form.getlist("projects"):
        try:
            selected_ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue

    if not selected_ids:
        return set()

    owned_projects = (
        Project.query
        .filter(
            project_scope_filter(Project),
            Project.id.in_(selected_ids),
        )
        .all()
    )

    return {
        project.id
        for project in owned_projects
    }


def _cleanup_saved_documents(storage_keys):
    for storage_key in storage_keys:
        cleanup_saved_document(storage_key)


def _parse_optional_date(raw_value):
    if not raw_value:
        return None

    try:
        return datetime.strptime(
            raw_value,
            "%Y-%m-%d"
        ).date()
    except ValueError as exc:
        raise ValueError("Invalid date format.") from exc


def _form_timezone():
    timezone_name = (
        request.form.get("timezone")
        or getattr(current_user, "timezone", None)
        or "US/Eastern"
    ).strip()

    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Invalid timezone.") from exc

    return timezone_name


def _render_add_sub(
    organization,
    projects,
    form_data=None,
    selected_projects=None,
):
    return render_template(
        "add_sub.html",
        sub=None,
        projects=projects,
        selected_projects=selected_projects or [],
        capacity_usage=get_organization_usage(organization),
        form_data=form_data,
    )


def _render_edit_sub(
    sub,
    projects,
    form_data=None,
    selected_projects=None,
):
    if selected_projects is None:
        selected_projects = [
            link.project_id
            for link in ProjectSubcontractor.query.filter_by(
                subcontractor_id=sub.id
            ).all()
        ]

    return render_template(
        "edit_sub.html",
        sub=sub,
        projects=projects,
        selected_projects=selected_projects,
        form_data=form_data,
    )


@subcontractors_bp.route("/sub/<int:sub_id>/documents")
@login_required
def view_sub_documents(sub_id):

    sub = Subcontractor.query.filter_by(
        id=sub_id,
    ).filter(
        subcontractor_scope_filter(Subcontractor)
    ).first_or_404()

    documents = (
        Document.query
        .filter_by(sub_id=sub.id)
        .order_by(Document.uploaded_at.desc())
        .all()
    )

    return render_template(
        "view_sub_documents.html",
        sub=sub,
        documents=documents,
    )


@subcontractors_bp.route("/add_sub", methods=["GET", "POST"])
@login_required
def add_sub():

    organization = get_current_organization()

    if not organization:
        abort(403)

    projects = scoped_project_query().all()

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").lower().strip()
        phone = request.form.get("phone")
        role = request.form.get("role")
        project_ids = _selected_owned_project_ids()

        if not name:
            flash("Subcontractor name is required.", "danger")
            return _render_add_sub(
                organization,
                projects,
                request.form,
                project_ids,
            )

        coi_raw = request.form.get("coi_expiration")

        try:
            coi_expiration = _parse_optional_date(coi_raw)
        except ValueError:
            flash("Invalid date format.", "danger")
            return _render_add_sub(
                organization,
                projects,
                request.form,
                project_ids,
            )

        try:
            timezone_name = _form_timezone()
        except ValueError:
            flash("Invalid timezone.", "danger")
            return _render_add_sub(
                organization,
                projects,
                request.form,
                project_ids,
            )

        try:
            require_subcontractor_capacity(organization)
        except PlanCapacityError as error:
            flash(error.check.message, "warning")
            return redirect(url_for("subcontractors.add_sub"))

        new_sub = Subcontractor(
            name=name,
            email=email,
            phone=phone,
            role=role,
            timezone=timezone_name,
            coi_expiration=coi_expiration,
            user_id=current_user.id,
            organization_id=organization.id,
        )

        db.session.add(new_sub)
        db.session.flush()

        for pid in project_ids:
            link = ProjectSubcontractor(
                project_id=pid,
                subcontractor_id=new_sub.id,
            )
            db.session.add(link)

        uploaded_docs = []
        saved_storage_keys = []

        files = request.files.getlist("documents")

        for file in files:

            if not file or file.filename == "":
                continue

            if not allowed_file(file.filename):
                flash(f"Invalid file type: {file.filename}", "danger")
                continue

            try:
                original_name = secure_filename(file.filename)

                doc_type = (
                    request.form.get("doc_type")
                    or "Document"
                )

                existing_doc = (
                    Document.query
                    .filter_by(
                        sub_id=new_sub.id,
                        document_type=doc_type,
                    )
                    .order_by(Document.version.desc())
                    .first()
                )

                new_version = (
                    existing_doc.version + 1
                    if existing_doc
                    else 1
                )

                unique_name = f"{uuid.uuid4().hex}_{original_name}"

                storage_key = save_document_file(
                    file,
                    unique_name,
                    sub_id=new_sub.id,
                )
                saved_storage_keys.append(storage_key)

                new_doc = Document(
                    filename=storage_key,
                    original_name=original_name,
                    document_type=doc_type,
                    version=new_version,
                    sub_id=new_sub.id,
                    uploaded_by=current_user.id,
                )

                db.session.add(new_doc)
                uploaded_docs.append(new_doc)

            except Exception as e:
                logger.exception(
                    "Subcontractor document upload failed during add_sub"
                )
                flash(f"Error uploading {file.filename}", "danger")

        try:
            db.session.commit()

            for doc in uploaded_docs:
                analyze_and_save_document(doc.id)

            flash("Subcontractor added successfully!", "success")

        except Exception as e:
            db.session.rollback()
            _cleanup_saved_documents(saved_storage_keys)
            logger.exception("Subcontractor create failed")
            flash("Error adding subcontractor.", "danger")

        return redirect(url_for("dashboard.dashboard"))

    return _render_add_sub(organization, projects)


@subcontractors_bp.route("/edit_sub/<int:id>", methods=["GET", "POST"])
@login_required
def edit_sub(id):

    sub = Subcontractor.query.filter_by(
        id=id,
    ).filter(
        subcontractor_scope_filter(Subcontractor)
    ).first_or_404()

    projects = scoped_project_query().all()

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").lower().strip()
        phone = request.form.get("phone")
        role = request.form.get("role")
        project_ids = _selected_owned_project_ids()

        if not name:
            flash("Subcontractor name is required.", "danger")
            return _render_edit_sub(
                sub,
                projects,
                request.form,
                project_ids,
            )

        expiration_raw = request.form.get("coi_expiration")

        try:
            coi_expiration = _parse_optional_date(expiration_raw)
        except ValueError:
            flash("Invalid date format.", "danger")
            return _render_edit_sub(
                sub,
                projects,
                request.form,
                project_ids,
            )

        try:
            timezone_name = _form_timezone()
        except ValueError:
            flash("Invalid timezone.", "danger")
            return _render_edit_sub(
                sub,
                projects,
                request.form,
                project_ids,
            )

        sub.name = name
        sub.email = email
        sub.phone = phone
        sub.role = role
        sub.timezone = timezone_name
        sub.coi_expiration = coi_expiration

        current_links = ProjectSubcontractor.query.filter_by(
            subcontractor_id=sub.id
        ).all()

        current_project_ids = [
            link.project_id
            for link in current_links
        ]

        for link in current_links:
            if link.project_id not in project_ids:
                db.session.delete(link)

        for pid in project_ids:
            if pid in current_project_ids:
                continue

            link = ProjectSubcontractor(
                project_id=pid,
                subcontractor_id=sub.id,
            )
            db.session.add(link)

        uploaded_docs = []
        saved_storage_keys = []

        files = request.files.getlist("documents")

        for file in files:

            if not file or file.filename == "":
                continue

            if not allowed_file(file.filename):
                flash(f"Invalid file type: {file.filename}", "danger")
                continue

            try:
                original_name = secure_filename(file.filename)

                doc_type = (
                    request.form.get("doc_type")
                    or "Document"
                )

                existing_doc = (
                    Document.query
                    .filter_by(
                        sub_id=sub.id,
                        document_type=doc_type,
                    )
                    .order_by(Document.version.desc())
                    .first()
                )

                new_version = (
                    existing_doc.version + 1
                    if existing_doc
                    else 1
                )

                unique_name = f"{uuid.uuid4().hex}_{original_name}"

                storage_key = save_document_file(
                    file,
                    unique_name,
                    sub_id=sub.id,
                )
                saved_storage_keys.append(storage_key)

                new_doc = Document(
                    filename=storage_key,
                    original_name=original_name,
                    document_type=doc_type,
                    version=new_version,
                    sub_id=sub.id,
                    uploaded_by=current_user.id,
                )

                db.session.add(new_doc)
                uploaded_docs.append(new_doc)

            except Exception as e:
                logger.exception(
                    "Subcontractor document upload failed during edit_sub"
                )
                flash(f"Error uploading {file.filename}", "danger")

        try:
            db.session.commit()

            for doc in uploaded_docs:
                analyze_and_save_document(doc.id)

            flash("Subcontractor updated successfully.", "success")

        except Exception as e:
            db.session.rollback()
            _cleanup_saved_documents(saved_storage_keys)
            logger.exception(
                "Subcontractor update failed for subcontractor_id=%s",
                sub.id,
            )
            flash("Error updating subcontractor.", "danger")

        return redirect(url_for("dashboard.dashboard"))

    return _render_edit_sub(sub, projects)


@subcontractors_bp.route("/delete_sub/<int:id>", methods=["POST"])
@login_required
def delete_sub(id):

    sub = Subcontractor.query.filter_by(
        id=id,
    ).filter(
        subcontractor_scope_filter(Subcontractor)
    ).first_or_404()

    try:
        for doc in sub.documents:
            delete_document_file(doc)

        db.session.delete(sub)
        db.session.commit()

        flash("Subcontractor deleted successfully.", "success")

    except Exception as e:
        db.session.rollback()
        logger.exception(
            "Subcontractor delete failed for subcontractor_id=%s",
            sub.id,
        )
        flash("Error deleting subcontractor.", "danger")

    return redirect(url_for("dashboard.dashboard"))
