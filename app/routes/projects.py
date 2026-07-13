import uuid

from collections import defaultdict
from datetime import datetime
import logging
from types import SimpleNamespace

from app.services.projects.project_ai_summary_service import (
    get_project_ai_summary,
)
from flask import (
    Blueprint,
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

from sqlalchemy.orm import joinedload

from werkzeug.utils import secure_filename

from app.extensions import db

from app.models import (
    Document,
    Project,
    ProjectSubcontractor,
    Subcontractor,
)

from app.routes.subcontractors import allowed_file

from app.services.documents.storage import (
    cleanup_saved_document,
    delete_document_file,
    save_document_file,
)

from app.services.documents.types import (
    PROJECT_DOCUMENT_TYPES,
    normalize_project_document_type,
)
from app.services.compliance_officer import (
    generate_compliance_advice,
)


projects_bp = Blueprint(
    "projects",
    __name__,
)

logger = logging.getLogger(__name__)


def _selected_owned_subcontractor_ids():
    selected_ids = []

    for raw_id in request.form.getlist("subcontractors"):
        try:
            selected_ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue

    if not selected_ids:
        return set()

    owned_subs = (
        Subcontractor.query
        .filter(
            Subcontractor.user_id == current_user.id,
            Subcontractor.id.in_(selected_ids),
        )
        .all()
    )

    return {
        sub.id
        for sub in owned_subs
    }


def _add_missing_project_links(project, subcontractor_ids):
    existing_ids = {
        link.subcontractor_id
        for link in ProjectSubcontractor.query.filter_by(
            project_id=project.id
        ).all()
    }

    for subcontractor_id in subcontractor_ids:
        if subcontractor_id in existing_ids:
            continue

        db.session.add(
            ProjectSubcontractor(
                project_id=project.id,
                subcontractor_id=subcontractor_id,
                coverage_limit=0,
            )
        )


def _project_subcontractor_view_model(project_subcontractor):
    advice_available = True

    try:
        advice = generate_compliance_advice(project_subcontractor)
    except Exception:
        logger.error(
            "Compliance advice unavailable for project_subcontractor_id=%s",
            getattr(
                project_subcontractor,
                "id",
                None,
            ),
        )
        advice_available = False
        advice = _fallback_compliance_advice(project_subcontractor)

    return {
        "project_subcontractor": project_subcontractor,
        "advice": advice,
        "advice_available": advice_available,
    }


def _fallback_compliance_advice(project_subcontractor):
    return SimpleNamespace(
        status=project_subcontractor.readiness_status,
        summary="Compliance advice unavailable.",
        actions=(),
    )


def _cleanup_saved_documents(storage_keys):
    for storage_key in storage_keys:
        cleanup_saved_document(storage_key)


@projects_bp.route("/add_project", methods=["GET", "POST"])
@login_required
def add_project():

    subs = Subcontractor.query.filter_by(user_id=current_user.id).all()

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        value_raw = request.form.get("contract_value")
        start_raw = request.form.get("start_date")
        end_raw = request.form.get("end_date")

        if not name:
            flash("Project name is required.", "danger")
            return redirect(url_for("projects.add_project"))

        try:
            contract_value = float(value_raw) if value_raw else 0
        except ValueError:
            flash("Invalid contract value.", "danger")
            return redirect(url_for("projects.add_project"))

        start_date = None
        if start_raw:
            try:
                start_date = datetime.strptime(start_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("Invalid start date.", "danger")
                return redirect(url_for("projects.add_project"))

        end_date = None
        if end_raw:
            try:
                end_date = datetime.strptime(end_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("Invalid end date.", "danger")
                return redirect(url_for("projects.add_project"))

        if start_date and end_date and end_date < start_date:
            flash("End date cannot be before start date.", "danger")
            return redirect(url_for("projects.add_project"))

        project = Project(
            name=name,
            contract_value=contract_value,
            user_id=current_user.id,
            start_date=start_date,
            end_date=end_date,
        )

        db.session.add(project)
        db.session.flush()

        _add_missing_project_links(
            project,
            _selected_owned_subcontractor_ids(),
        )

        files = request.files.getlist("documents")
        doc_type = normalize_project_document_type(
            request.form.get("doc_type")
        )
        next_versions = defaultdict(lambda: 1)
        saved_storage_keys = []

        for file in files:

            if not file or file.filename == "":
                continue

            if not allowed_file(file.filename):
                flash(f"Invalid file type: {file.filename}", "danger")
                continue

            try:
                original_name = file.filename
                safe_name = secure_filename(original_name)
                unique_name = f"{uuid.uuid4().hex}_{safe_name}"

                storage_key = save_document_file(
                    file,
                    unique_name,
                    project_id=project.id,
                )
                saved_storage_keys.append(storage_key)

                doc = Document(
                    filename=storage_key,
                    original_name=original_name,
                    document_type=doc_type,
                    version=next_versions[doc_type],
                    project_id=project.id,
                )

                db.session.add(doc)
                next_versions[doc_type] += 1

            except Exception as e:
                logger.exception(
                    "Project document upload failed during project create"
                )
                flash(f"Error uploading {file.filename}", "danger")

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            _cleanup_saved_documents(saved_storage_keys)
            logger.exception("Project create failed")
            flash("Error creating project.", "danger")
            return redirect(url_for("projects.add_project"))

        flash("Project created successfully", "success")
        return redirect(url_for("dashboard.dashboard"))

    return render_template(
        "add_project.html",
        subs=subs,
        project_document_types=PROJECT_DOCUMENT_TYPES,
    )


@projects_bp.route("/edit_project/<int:project_id>", methods=["GET", "POST"])
@login_required
def edit_project(project_id):

    project = Project.query.filter_by(
        id=project_id,
        user_id=current_user.id,
    ).first_or_404()

    subs = Subcontractor.query.filter_by(user_id=current_user.id).all()

    if request.method == "POST":

        project.name = request.form.get("name", "").strip()

        if not project.name:
            flash("Project name is required.", "danger")
            return redirect(
                url_for(
                    "projects.edit_project",
                    project_id=project.id,
                )
            )

        value_raw = request.form.get("contract_value")

        try:
            project.contract_value = float(value_raw) if value_raw else 0
        except ValueError:
            flash("Invalid contract value.", "danger")
            return redirect(
                url_for(
                    "projects.edit_project",
                    project_id=project.id,
                )
            )

        start_raw = request.form.get("start_date")
        end_raw = request.form.get("end_date")

        try:
            start_date = (
                datetime.strptime(start_raw, "%Y-%m-%d").date()
                if start_raw
                else None
            )

            end_date = (
                datetime.strptime(end_raw, "%Y-%m-%d").date()
                if end_raw
                else None
            )

        except ValueError:
            flash("Invalid date format.", "danger")
            return redirect(
                url_for(
                    "projects.edit_project",
                    project_id=project.id,
                )
            )

        if start_date and end_date and end_date < start_date:
            flash("End date cannot be before start date.", "danger")
            return redirect(
                url_for(
                    "projects.edit_project",
                    project_id=project.id,
                )
            )

        project.start_date = start_date
        project.end_date = end_date

        selected_sub_ids = _selected_owned_subcontractor_ids()

        current_links = ProjectSubcontractor.query.filter_by(
            project_id=project.id
        ).all()

        current_sub_ids = [
            link.subcontractor_id
            for link in current_links
        ]

        for link in current_links:
            if link.subcontractor_id not in selected_sub_ids:
                db.session.delete(link)

        for sub_id in selected_sub_ids:
            if sub_id not in current_sub_ids:
                new_link = ProjectSubcontractor(
                    project_id=project.id,
                    subcontractor_id=sub_id,
                    coverage_limit=0,
                )
                db.session.add(new_link)

        saved_storage_keys = []
        file = request.files.get("file")

        if file and file.filename != "":

            if allowed_file(file.filename):

                original_name = file.filename
                safe_name = secure_filename(original_name)
                unique_name = f"{uuid.uuid4().hex}_{safe_name}"

                storage_key = save_document_file(
                    file,
                    unique_name,
                    project_id=project.id,
                )
                saved_storage_keys.append(storage_key)

                doc_type = normalize_project_document_type(
                    request.form.get("doc_type")
                )

                last_doc = (
                    Document.query
                    .filter_by(
                        project_id=project.id,
                        document_type=doc_type,
                    )
                    .order_by(Document.version.desc())
                    .first()
                )

                version = last_doc.version + 1 if last_doc else 1

                new_doc = Document(
                    filename=storage_key,
                    original_name=original_name,
                    document_type=doc_type,
                    version=version,
                    project_id=project.id,
                )

                db.session.add(new_doc)

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            _cleanup_saved_documents(saved_storage_keys)
            logger.exception(
                "Project update failed for project_id=%s",
                project.id,
            )

            flash("Error updating project.", "danger")

            return redirect(
                url_for(
                    "projects.edit_project",
                    project_id=project.id,
                )
            )

        flash("Project updated successfully!", "success")

        return redirect(
            url_for(
                "projects.view_project",
                project_id=project.id,
            )
        )

    return render_template(
        "edit_project.html",
        project=project,
        subs=subs,
        project_document_types=PROJECT_DOCUMENT_TYPES,
    )


@projects_bp.route("/project/<int:project_id>")
@login_required
def view_project(project_id):

    project = (
        Project.query
        .filter_by(
            id=project_id,
            user_id=current_user.id,
        )
        .first_or_404()
    )

    ai_summary = get_project_ai_summary(
        project.id
    )

    links = (
        ProjectSubcontractor.query
        .options(joinedload(ProjectSubcontractor.subcontractor))
        .filter_by(project_id=project.id)
        .all()
    )

    subcontractor_rows = [
        _project_subcontractor_view_model(link)
        for link in links
    ]

    docs = (
        Document.query
        .filter_by(project_id=project.id)
        .order_by(
            Document.document_type,
            Document.version.desc(),
        )
        .all()
    )

    documents = defaultdict(list)

    for doc in docs:
        documents[doc.document_type].append(doc)

    return render_template(
        "view_project.html",
        project=project,
        subcontractor_rows=subcontractor_rows,
        documents=dict(documents),
        ai_summary=ai_summary,
    )


@projects_bp.route("/delete_project/<int:id>", methods=["POST"])
@login_required
def delete_project(id):

    project = Project.query.filter_by(
        id=id,
        user_id=current_user.id,
    ).first_or_404()

    try:
        documents = Document.query.filter_by(project_id=project.id).all()

        for doc in documents:
            delete_document_file(doc)
            db.session.delete(doc)

        links = ProjectSubcontractor.query.filter_by(
            project_id=project.id
        ).all()

        for link in links:
            db.session.delete(link)

        db.session.delete(project)
        db.session.commit()

        flash("Project deleted successfully", "success")

    except Exception as e:
        db.session.rollback()
        logger.exception(
            "Project delete failed for project_id=%s",
            project.id,
        )
        flash("Error deleting project.", "danger")

    return redirect(url_for("dashboard.dashboard"))


@projects_bp.route("/project/<int:project_id>/upload", methods=["POST"])
@login_required
def upload_project_document(project_id):

    project = Project.query.filter_by(
        id=project_id,
        user_id=current_user.id,
    ).first_or_404()

    file = request.files.get("file")

    if not file or file.filename == "":
        flash("No file selected.", "danger")
        return redirect(
            url_for(
                "projects.view_project",
                project_id=project.id,
            )
        )

    if not allowed_file(file.filename):
        flash("Invalid file type.", "danger")
        return redirect(
            url_for(
                "projects.view_project",
                project_id=project.id,
            )
        )

    doc_type = normalize_project_document_type(
        request.form.get("doc_type")
    )

    original_name = file.filename
    safe_name = secure_filename(original_name)
    unique_name = f"{uuid.uuid4().hex}_{safe_name}"

    try:
        storage_key = save_document_file(
            file,
            unique_name,
            project_id=project.id,
        )
    except Exception as e:
        logger.exception(
            "Project document storage save failed for project_id=%s",
            project.id,
        )
        flash("Error uploading file.", "danger")
        return redirect(
            url_for(
                "projects.view_project",
                project_id=project.id,
            )
        )

    last_doc = (
        Document.query
        .filter_by(
            project_id=project.id,
            document_type=doc_type,
        )
        .order_by(Document.version.desc())
        .first()
    )

    version = last_doc.version + 1 if last_doc else 1

    new_doc = Document(
        filename=storage_key,
        original_name=original_name,
        document_type=doc_type,
        version=version,
        project_id=project.id,
    )

    db.session.add(new_doc)

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        cleanup_saved_document(storage_key)
        logger.exception(
            "Project document commit failed for project_id=%s",
            project.id,
        )
        flash("Error uploading file.", "danger")
        return redirect(
            url_for(
                "projects.view_project",
                project_id=project.id,
            )
        )

    flash("Document uploaded successfully!", "success")

    return redirect(
        url_for(
            "projects.view_project",
            project_id=project.id,
        )
    )
