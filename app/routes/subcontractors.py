import os
import uuid

from datetime import datetime

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

from werkzeug.utils import secure_filename

from app.extensions import db

from app.models import (
    Document,
    Project,
    ProjectSubcontractor,
    Subcontractor,
)


subcontractors_bp = Blueprint(
    "subcontractors",
    __name__,
)


# ==========================
# FILE VALIDATION
# ==========================

def allowed_file(filename):

    allowed_extensions = current_app.config.get(
        "ALLOWED_EXTENSIONS",
        {"pdf", "jpg", "jpeg", "png"}
    )

    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in allowed_extensions
    )


# ==========================
# VIEW SUB DOCUMENTS
# ==========================

@subcontractors_bp.route("/sub/<int:sub_id>/documents")
@login_required
def view_sub_documents(sub_id):

    sub = Subcontractor.query.filter_by(
        id=sub_id,
        user_id=current_user.id
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


# ==========================
# ADD SUBCONTRACTOR
# ==========================

@subcontractors_bp.route(
    "/add_sub",
    methods=["GET", "POST"]
)
@login_required
def add_sub():

    projects = Project.query.filter_by(
        user_id=current_user.id
    ).all()

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").lower().strip()
        phone = request.form.get("phone")
        role = request.form.get("role")

        if not name:
            flash(
                "Subcontractor name is required.",
                "danger"
            )
            return redirect(
                url_for("subcontractors.add_sub")
            )

        coi_raw = request.form.get("coi_expiration")

        if coi_raw:

            try:
                coi_expiration = datetime.strptime(
                    coi_raw,
                    "%Y-%m-%d"
                ).date()

            except ValueError:
                flash(
                    "Invalid date format.",
                    "danger"
                )
                return redirect(
                    url_for("subcontractors.add_sub")
                )

        else:
            coi_expiration = None

        new_sub = Subcontractor(
            name=name,
            email=email,
            phone=phone,
            role=role,
            timezone=current_user.timezone,
            coi_expiration=coi_expiration,
            user_id=current_user.id,
        )

        db.session.add(new_sub)
        db.session.flush()

        # ==========================
        # LINK SUB TO PROJECTS
        # ==========================

        project_ids = request.form.getlist("projects")

        for pid in set(project_ids):

            link = ProjectSubcontractor(
                project_id=int(pid),
                subcontractor_id=new_sub.id,
            )

            db.session.add(link)

        # ==========================
        # DOCUMENT UPLOAD
        # ==========================

        files = request.files.getlist("documents")

        for file in files:

            if not file or file.filename == "":
                continue

            if not allowed_file(file.filename):
                flash(
                    f"Invalid file type: {file.filename}",
                    "danger"
                )
                continue

            try:

                original_name = secure_filename(
                    file.filename
                )

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

                unique_name = (
                    f"{uuid.uuid4().hex}_{original_name}"
                )

                path = os.path.join(
                    current_app.config["UPLOAD_FOLDER"],
                    unique_name,
                )

                file.save(path)

                new_doc = Document(
                    filename=unique_name,
                    original_name=original_name,
                    document_type=doc_type,
                    version=new_version,
                    sub_id=new_sub.id,
                    uploaded_by=current_user.id,
                )

                db.session.add(new_doc)

            except Exception as e:
                print("UPLOAD ERROR:", e)
                flash(
                    f"Error uploading {file.filename}",
                    "danger"
                )

        db.session.commit()

        flash(
            "Subcontractor added successfully!",
            "success"
        )

        return redirect(
            url_for("dashboard.dashboard")
        )

    return render_template(
        "add_sub.html",
        sub=None,
        projects=projects,
        selected_projects=[],
    )


# ==========================
# EDIT SUBCONTRACTOR
# ==========================

@subcontractors_bp.route(
    "/edit_sub/<int:id>",
    methods=["GET", "POST"]
)
@login_required
def edit_sub(id):

    sub = Subcontractor.query.filter_by(
        id=id,
        user_id=current_user.id
    ).first_or_404()

    projects = Project.query.filter_by(
        user_id=current_user.id
    ).all()

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").lower().strip()
        phone = request.form.get("phone")
        role = request.form.get("role")

        if not name:
            flash(
                "Subcontractor name is required.",
                "danger"
            )
            return redirect(
                url_for(
                    "subcontractors.edit_sub",
                    id=sub.id
                )
            )

        sub.name = name
        sub.email = email
        sub.phone = phone
        sub.role = role
        sub.timezone = current_user.timezone

        expiration_raw = request.form.get("coi_expiration")

        if expiration_raw:

            try:
                sub.coi_expiration = datetime.strptime(
                    expiration_raw,
                    "%Y-%m-%d"
                ).date()

            except ValueError:
                flash(
                    "Invalid date format.",
                    "danger"
                )
                return redirect(
                    url_for(
                        "subcontractors.edit_sub",
                        id=sub.id
                    )
                )

        else:
            sub.coi_expiration = None

        # ==========================
        # UPDATE PROJECT LINKS
        # ==========================

        project_ids = request.form.getlist("projects")

        ProjectSubcontractor.query.filter_by(
            subcontractor_id=sub.id
        ).delete(
            synchronize_session=False
        )

        for pid in project_ids:

            link = ProjectSubcontractor(
                project_id=int(pid),
                subcontractor_id=sub.id,
            )

            db.session.add(link)

        try:
            db.session.commit()

            flash(
                "Subcontractor updated successfully.",
                "success"
            )

        except Exception as e:
            db.session.rollback()

            print("EDIT SUB ERROR:", e)

            flash(
                "Error updating subcontractor.",
                "danger"
            )

        return redirect(
            url_for("dashboard.dashboard")
        )

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
    )


# ==========================
# DELETE SUBCONTRACTOR
# ==========================

@subcontractors_bp.route(
    "/delete_sub/<int:id>",
    methods=["POST"]
)
@login_required
def delete_sub(id):

    sub = Subcontractor.query.filter_by(
        id=id,
        user_id=current_user.id
    ).first_or_404()

    try:

        for doc in sub.documents:

            file_path = os.path.join(
                current_app.config["UPLOAD_FOLDER"],
                doc.filename,
            )

            if os.path.exists(file_path):

                try:
                    os.remove(file_path)

                except Exception as e:
                    print("FILE DELETE ERROR:", e)

        db.session.delete(sub)
        db.session.commit()

        flash(
            "Subcontractor deleted successfully.",
            "success"
        )

    except Exception as e:

        db.session.rollback()

        print("DELETE SUB ERROR:", e)

        flash(
            "Error deleting subcontractor.",
            "danger"
        )

    return redirect(
        url_for("dashboard.dashboard")
    )