import os
import uuid

from collections import defaultdict
from datetime import datetime
from app.services.projects.project_ai_summary_service import (
    get_project_ai_summary,
)
from flask import (
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


projects_bp = Blueprint(
    "projects",
    __name__,
)


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

        os.makedirs(current_app.config["UPLOAD_FOLDER"], exist_ok=True)

        files = request.files.getlist("documents")

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

                filepath = os.path.join(
                    current_app.config["UPLOAD_FOLDER"],
                    unique_name,
                )

                file.save(filepath)

                doc = Document(
                    filename=unique_name,
                    original_name=original_name,
                    document_type="Project Document",
                    project_id=project.id,
                )

                db.session.add(doc)

            except Exception as e:
                print("UPLOAD ERROR:", e)
                flash(f"Error uploading {file.filename}", "danger")

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print("PROJECT CREATE ERROR:", e)
            flash("Error creating project.", "danger")
            return redirect(url_for("projects.add_project"))

        flash("Project created successfully", "success")
        return redirect(url_for("dashboard.dashboard"))

    return render_template(
        "add_project.html",
        subs=subs,
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

        selected_subs = request.form.getlist("subcontractors")

        current_links = ProjectSubcontractor.query.filter_by(
            project_id=project.id
        ).all()

        current_sub_ids = [
            str(link.subcontractor_id)
            for link in current_links
        ]

        for link in current_links:
            if str(link.subcontractor_id) not in selected_subs:
                db.session.delete(link)

        for sub_id in selected_subs:
            if sub_id not in current_sub_ids:
                sub = Subcontractor.query.filter_by(
                    id=sub_id,
                    user_id=current_user.id,
                ).first()

                if sub:
                    new_link = ProjectSubcontractor(
                        project_id=project.id,
                        subcontractor_id=sub.id,
                        coverage_limit=0,
                    )
                    db.session.add(new_link)

        file = request.files.get("file")

        if file and file.filename != "":

            if allowed_file(file.filename):

                original_name = file.filename
                safe_name = secure_filename(original_name)
                unique_name = f"{uuid.uuid4().hex}_{safe_name}"

                path = os.path.join(
                    current_app.config["UPLOAD_FOLDER"],
                    unique_name,
                )

                file.save(path)

                doc_type = request.form.get("doc_type", "Document")

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
                    filename=unique_name,
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
            print("PROJECT UPDATE ERROR:", e)

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
        links=links,
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

            file_path = os.path.join(
                current_app.config["UPLOAD_FOLDER"],
                doc.filename,
            )

            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e:
                    print("FILE DELETE ERROR:", e)

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
        print("DELETE PROJECT ERROR:", e)
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

    doc_type = request.form.get("doc_type") or "Document"

    original_name = secure_filename(file.filename)
    unique_name = f"{uuid.uuid4().hex}_{original_name}"

    upload_folder = os.path.join(
        current_app.config["UPLOAD_FOLDER"],
        f"project_{project.id}",
    )

    os.makedirs(upload_folder, exist_ok=True)

    file_path = os.path.join(upload_folder, unique_name)

    try:
        file.save(file_path)
    except Exception as e:
        print("Upload error:", e)
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
        filename=unique_name,
        original_name=original_name,
        document_type=doc_type,
        version=version,
        project_id=project.id,
    )

    db.session.add(new_doc)
    db.session.commit()

    flash("Document uploaded successfully!", "success")

    return redirect(
        url_for(
            "projects.view_project",
            project_id=project.id,
        )
    )