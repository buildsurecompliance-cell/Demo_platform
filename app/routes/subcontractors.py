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
from sqlalchemy.orm import joinedload, selectinload

from app.decorators import subscription_required
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
from app.services.compliance_evidence_service import (
    collect_coi_evidence,
    extract_coi_coverage,
)
from app.services.readiness_service import calculate_readiness

from app.services.documents.storage import (
    cleanup_saved_document,
    delete_document_file,
    save_document_file,
)
from app.services.documents.types import (
    SUBCONTRACTOR_DOCUMENT_TYPE,
)
from app.services.subcontractors.coi_summary import (
    get_subcontractor_coi_summary,
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


def _money_label(value):
    if not value:
        return "Not available"

    amount = float(value)

    if amount >= 1_000_000 and amount % 1_000_000 == 0:
        return f"${amount / 1_000_000:.0f}M"

    return f"${amount:,.0f}"


def _subcontractor_list_row(sub):
    summary = get_subcontractor_coi_summary(sub)

    return {
        "subcontractor": sub,
        "company": sub.name,
        "trade": sub.role or "Not specified",
        "expiration": (
            summary.expiration.strftime("%m/%d/%Y")
            if summary.expiration
            else "Not available"
        ),
        "coverage": (
            _money_label(summary.coverage)
            if summary.has_coverage
            else "Not available"
        ),
        "status": summary.status,
        "project_count": len(sub.projects),
        "document_count": len(sub.documents),
    }


def _coverage_gap_label(current_coverage, required_coverage):
    if not required_coverage:
        return "No project minimum"

    if current_coverage is None:
        return _money_label(required_coverage)

    gap = max(
        int(required_coverage) - int(current_coverage or 0),
        0,
    )

    if not gap:
        return "No gap"

    return _money_label(gap)


def _doc_status_label(status):
    if status == "analyzed":
        return "Analyzed"

    if status == "failed":
        return "Failed"

    return "Not Analyzed"


def _coi_document_view_model(doc, sub, readiness_impacts):
    extracted = doc.ai_extracted_data or {}
    compliance = doc.ai_compliance_result or {}
    general_liability = extracted.get("general_liability") or {}

    return {
        "document": doc,
        "status": _doc_status_label(doc.ai_status),
        "expiration": (
            extracted.get("expiration_date")
            or (
                sub.coi_expiration.strftime("%m/%d/%Y")
                if sub.coi_expiration
                else "Not available"
            )
        ),
        "general_liability": _money_label(
            extract_coi_coverage(extracted)
        ),
        "general_liability_each_occurrence": _money_label(
            general_liability.get("each_occurrence")
        ),
        "general_liability_general_aggregate": _money_label(
            general_liability.get("general_aggregate")
        ),
        "general_liability_products_completed_operations": _money_label(
            general_liability.get("products_completed_operations")
        ),
        "confidence": _confidence_label(
            extracted.get("confidence")
            or doc.ai_confidence
        ),
        "evidence_state": compliance.get("status") or "Not available",
        "issues": compliance.get("issues") or [],
        "warnings": compliance.get("warnings") or [],
        "readiness_impacts": readiness_impacts,
    }


def _confidence_label(value):
    if value is None:
        return "Not available"

    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return "Not available"

    if confidence <= 1:
        confidence *= 100

    return f"{confidence:.0f}%"


def _sub_readiness_impacts(sub):
    impacts = []
    evidence_coverage = _active_coverage_for_subcontractor(sub)

    for link in sub.projects:
        readiness = calculate_readiness(link)
        reason = (
            readiness["reasons"][0]["message"]
            if readiness.get("reasons")
            else "No blocking issue."
        )
        impacts.append(
            {
                "project": link.project.name if link.project else "Project",
                "status": readiness["status"],
                "reason": reason,
                "current_coverage": _money_label(
                    _conservative_coverage(
                        evidence_coverage,
                        link.coverage_limit,
                    )
                ),
                "required_coverage": (
                    _money_label(link.project.required_coverage)
                    if link.project and link.project.required_coverage
                    else "No minimum"
                ),
                "coverage_gap": _coverage_gap_label(
                    _conservative_coverage(
                        evidence_coverage,
                        link.coverage_limit,
                    ),
                    link.project.required_coverage if link.project else None,
                ),
            }
        )

    return impacts


def _active_coverage_for_subcontractor(sub):
    validated_evidence = [
        item
        for item in collect_coi_evidence(sub)
        if item.validated
    ]

    if not validated_evidence:
        return None

    return validated_evidence[0].value.get("coverage")


def _conservative_coverage(evidence_coverage, manual_coverage):
    return evidence_coverage


def _analyze_uploaded_documents(document_ids):
    all_succeeded = True

    for document_id in document_ids:
        try:
            analysis = analyze_and_save_document(document_id)
        except Exception as error:
            db.session.rollback()
            doc = db.session.get(Document, document_id)

            if doc:
                doc.ai_status = "failed"
                doc.ai_error = "Document analysis failed."
                db.session.commit()

            logger.error(
                "Automatic subcontractor document analysis failed document_id=%s error_type=%s",
                document_id,
                error.__class__.__name__,
            )
            all_succeeded = False
            continue

        if not analysis.get("success"):
            all_succeeded = False

    return all_succeeded


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


def _default_subcontractor_timezone():
    timezone_name = (
        getattr(current_user, "timezone", None)
        or "US/Eastern"
    ).strip()

    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return "US/Eastern"

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
        coi_summary=get_subcontractor_coi_summary(sub),
    )


@subcontractors_bp.route("/subcontractors")
@login_required
@subscription_required
def list_subcontractors():
    search = request.args.get(
        "search",
        "",
    ).strip()
    status_filter = request.args.get(
        "status",
        "",
    ).strip().upper()

    query = scoped_subcontractor_query().options(
        selectinload(Subcontractor.documents),
        selectinload(Subcontractor.projects),
    )

    if search:
        query = query.filter(
            Subcontractor.name.ilike(f"%{search}%")
        )

    subs = query.order_by(
        Subcontractor.name.asc()
    ).all()

    rows = [
        _subcontractor_list_row(sub)
        for sub in subs
    ]

    valid_statuses = {
        "VALID",
        "EXPIRED",
        "MISSING",
        "CHECKING",
    }
    if status_filter in valid_statuses:
        rows = [
            row
            for row in rows
            if row["status"] == status_filter
        ]
    else:
        status_filter = ""

    return render_template(
        "subcontractors_list.html",
        sub_rows=rows,
        search=search,
        status_filter=status_filter,
    )


@subcontractors_bp.route("/sub/<int:sub_id>/documents")
@login_required
@subscription_required
def view_sub_documents(sub_id):

    sub = Subcontractor.query.filter_by(
        id=sub_id,
    ).options(
        joinedload(Subcontractor.projects)
        .joinedload(ProjectSubcontractor.project),
    ).filter(
        subcontractor_scope_filter(Subcontractor)
    ).first_or_404()

    documents = (
        Document.query
        .filter_by(sub_id=sub.id)
        .order_by(Document.uploaded_at.desc())
        .all()
    )

    readiness_impacts = _sub_readiness_impacts(sub)

    return render_template(
        "view_sub_documents.html",
        sub=sub,
        documents=documents,
        document_rows=[
            _coi_document_view_model(doc, sub, readiness_impacts)
            for doc in documents
        ],
    )


@subcontractors_bp.route("/add_sub", methods=["GET", "POST"])
@login_required
@subscription_required
def add_sub():

    organization = get_current_organization()

    if not organization:
        abort(403)

    projects = scoped_project_query().all()

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").lower().strip()
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

        try:
            require_subcontractor_capacity(organization)
        except PlanCapacityError as error:
            flash(error.check.message, "warning")
            return redirect(url_for("subcontractors.add_sub"))

        new_sub = Subcontractor(
            name=name,
            email=email,
            phone=None,
            role=role,
            timezone=_default_subcontractor_timezone(),
            coi_expiration=None,
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
                original_name = file.filename

                doc_type = SUBCONTRACTOR_DOCUMENT_TYPE

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

                storage_key = save_document_file(
                    file,
                    original_name,
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

        uploaded_doc_ids = []

        try:
            db.session.flush()
            uploaded_doc_ids = [
                doc.id
                for doc in uploaded_docs
            ]
            db.session.commit()

        except Exception as e:
            db.session.rollback()
            _cleanup_saved_documents(saved_storage_keys)
            logger.exception("Subcontractor create failed")
            flash("Error adding subcontractor.", "danger")
            return redirect(url_for("dashboard.dashboard"))

        if uploaded_doc_ids:
            if _analyze_uploaded_documents(uploaded_doc_ids):
                flash("Subcontractor added successfully!", "success")
            else:
                flash(
                    "Subcontractor added, but automatic COI analysis could not be completed. "
                    "You can retry from the documents page.",
                    "warning",
                )
        else:
            flash("Subcontractor added successfully!", "success")

        return redirect(url_for("dashboard.dashboard"))

    return _render_add_sub(organization, projects)


@subcontractors_bp.route("/edit_sub/<int:id>", methods=["GET", "POST"])
@login_required
@subscription_required
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

        sub.name = name
        sub.email = email
        sub.role = role

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

        try:
            db.session.commit()

        except Exception as e:
            db.session.rollback()
            logger.exception(
                "Subcontractor update failed for subcontractor_id=%s",
                sub.id,
            )
            flash("Error updating subcontractor.", "danger")
            return redirect(url_for("dashboard.dashboard"))

        flash("Subcontractor updated successfully.", "success")

        return redirect(url_for("dashboard.dashboard"))

    return _render_edit_sub(sub, projects)


@subcontractors_bp.route("/delete_sub/<int:id>", methods=["POST"])
@login_required
@subscription_required
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
