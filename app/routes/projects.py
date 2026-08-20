from collections import defaultdict
from datetime import datetime
import logging
from types import SimpleNamespace

from app.services.projects.project_ai_summary_service import (
    get_project_ai_summary,
)
from flask import (
    abort,
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

from app.decorators import subscription_required
from app.extensions import db

from app.models import (
    DOCUMENT_REQUEST_COMPLETED,
    DOCUMENT_REQUEST_PENDING,
    Document,
    DocumentRequest,
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
from app.services.document_analysis_service import (
    analyze_and_save_document,
)

from app.services.documents.types import (
    PROJECT_DOCUMENT_TYPES,
    SUBCONTRACTOR_DOCUMENT_TYPE,
    normalize_project_document_type,
    supports_automatic_analysis,
)
from app.services.compliance_officer import (
    generate_compliance_advice,
)
from app.services.compliance_evidence_service import (
    collect_coi_evidence,
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
    require_project_capacity,
)


projects_bp = Blueprint(
    "projects",
    __name__,
)

logger = logging.getLogger(__name__)

PROJECT_REQUIRED_COVERAGE_OPTIONS = [
    {
        "value": "",
        "label": "No minimum",
    },
    {
        "value": "1000000",
        "label": "$1M",
    },
    {
        "value": "2000000",
        "label": "$2M",
    },
    {
        "value": "5000000",
        "label": "$5M",
    },
    {
        "value": "custom",
        "label": "Custom amount",
    },
]

PROJECT_REQUIRED_COVERAGE_PRESETS = {
    "1000000": 1000000,
    "2000000": 2000000,
    "5000000": 5000000,
}

def _money_short(value):
    amount = float(value or 0)
    abs_amount = abs(amount)

    if abs_amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.1f}B"

    if abs_amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"

    if abs_amount >= 1_000:
        return f"${amount / 1_000:.1f}K"

    return f"${amount:,.0f}"


def _money_full(value):
    return f"${float(value or 0):,.0f}"


def _coverage_label(value):
    if not value:
        return "No minimum"

    amount = int(value)
    if amount % 1_000_000 == 0:
        return f"${amount // 1_000_000}M"

    return _money_full(amount)


def _mobilization_label(status):
    if status == "Ready to Mobilize":
        return "READY"

    if status == "Pending Compliance":
        return "PENDING"

    return "BLOCKED"


def _mobilization_message(label):
    messages = {
        "READY": "This project is ready to mobilize.",
        "PENDING": "This project requires review before mobilization.",
        "BLOCKED": "One or more subcontractors cannot work today.",
    }

    return messages[label]


def _parse_positive_integer_amount(raw_value):
    cleaned = (raw_value or "").strip().replace("$", "").replace(",", "")

    if cleaned == "":
        return None

    try:
        value = int(cleaned)
    except ValueError as exc:
        raise ValueError("Amount must be a whole number.") from exc

    if value < 0:
        raise ValueError("Amount cannot be negative.")

    if value == 0:
        return None

    return value


def _parse_required_coverage(form):
    selected = (form.get("required_coverage_choice") or "").strip()

    if selected in ("", "0"):
        return None

    if selected == "custom":
        return _parse_positive_integer_amount(
            form.get("required_coverage_custom")
        )

    if selected not in PROJECT_REQUIRED_COVERAGE_PRESETS:
        raise ValueError("Invalid coverage option.")

    return PROJECT_REQUIRED_COVERAGE_PRESETS[selected]


def _coverage_form_values(required_coverage=None, form_data=None):
    if form_data is not None:
        return {
            "choice": form_data.get("required_coverage_choice", ""),
            "custom": form_data.get("required_coverage_custom", ""),
        }

    if required_coverage is None:
        return {
            "choice": "",
            "custom": "",
        }

    coverage = int(required_coverage)
    coverage_text = str(coverage)

    if coverage_text in PROJECT_REQUIRED_COVERAGE_PRESETS:
        return {
            "choice": coverage_text,
            "custom": "",
        }

    return {
        "choice": "custom",
        "custom": coverage_text,
    }


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
            subcontractor_scope_filter(Subcontractor),
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

    current_coverage = _active_coverage_for_project_subcontractor(
        project_subcontractor
    )
    required_coverage = getattr(
        project_subcontractor.project,
        "required_coverage",
        None,
    )
    coverage_gap = None

    if current_coverage is not None and required_coverage:
        coverage_gap = max(
            int(required_coverage) - int(current_coverage or 0),
            0,
        )

    primary_action = advice.actions[0] if advice.actions else None
    latest_request = _latest_document_request(project_subcontractor)
    display_reason = _display_reason(
        advice.summary,
        current_coverage,
        required_coverage,
        coverage_gap,
    )
    display_action = _display_action(
        primary_action.description if primary_action else None,
        required_coverage,
        coverage_gap,
    )

    return {
        "project_subcontractor": project_subcontractor,
        "advice": advice,
        "advice_available": advice_available,
        "status": advice.status,
        "current_coverage": _coverage_label(current_coverage),
        "required_coverage": _coverage_label(required_coverage),
        "coverage_gap": (
            _coverage_label(coverage_gap)
            if coverage_gap
            else "No gap"
        ),
        "coi_expiration": (
            project_subcontractor.subcontractor.coi_expiration.strftime(
                "%m/%d/%Y"
            )
            if project_subcontractor.subcontractor.coi_expiration
            else "Not available"
        ),
        "primary_reason": display_reason,
        "recommended_action": display_action,
        "action_priority": (
            primary_action.priority
            if primary_action
            else "LOW"
        ),
        "document_request": latest_request,
        "document_request_status": _document_request_status_label(
            latest_request
        ),
        "document_request_cta": _document_request_cta_label(
            latest_request,
            advice.status,
        ),
        "show_operational_action": bool(display_action),
        "show_more_actions": not _has_coverage_gap(
            current_coverage,
            required_coverage,
            coverage_gap,
        ),
    }


def _fallback_compliance_advice(project_subcontractor):
    return SimpleNamespace(
        status=project_subcontractor.readiness_status,
        summary="Compliance advice unavailable.",
        actions=(),
    )


def _latest_document_request(project_subcontractor):
    return (
        DocumentRequest.query
        .filter_by(
            project_id=project_subcontractor.project_id,
            subcontractor_id=project_subcontractor.subcontractor_id,
            document_type=SUBCONTRACTOR_DOCUMENT_TYPE,
        )
        .order_by(DocumentRequest.created_at.desc())
        .first()
    )


def _display_reason(summary, current_coverage, required_coverage, coverage_gap):
    if _has_coverage_gap(current_coverage, required_coverage, coverage_gap):
        return "GL coverage is below requirement."

    return summary


def _display_action(action_description, required_coverage, coverage_gap):
    if coverage_gap and required_coverage:
        return (
            "A corrected COI with at least "
            f"{_coverage_label(required_coverage)} GL coverage is required."
        )

    return action_description


def _document_request_cta_label(document_request, status):
    if status == "READY":
        return None

    if (
        document_request
        and document_request.status == DOCUMENT_REQUEST_PENDING
    ):
        return "Resend"

    if (
        document_request
        and document_request.status == DOCUMENT_REQUEST_COMPLETED
    ):
        return "Request Corrected COI"

    return "Request COI"


def _document_request_status_label(document_request):
    if not document_request:
        return None

    if document_request.status == DOCUMENT_REQUEST_PENDING:
        if document_request.last_sent_at:
            return (
                "COI request sent "
                f"{_short_date_label(document_request.last_sent_at)}"
            )

        return "COI request pending"

    if document_request.status == DOCUMENT_REQUEST_COMPLETED:
        if document_request.completed_at:
            return (
                "COI received "
                f"{_short_date_label(document_request.completed_at)}"
            )

        return "COI received"

    return None


def _short_date_label(value):
    if not value:
        return ""

    return value.strftime("%b %d").replace(" 0", " ")


def _has_coverage_gap(current_coverage, required_coverage, coverage_gap):
    return (
        current_coverage is not None
        and required_coverage
        and coverage_gap
        and coverage_gap > 0
    )


def _active_coverage_for_project_subcontractor(project_subcontractor):
    subcontractor = getattr(
        project_subcontractor,
        "subcontractor",
        None,
    )

    if subcontractor:
        validated_evidence = [
            item
            for item in collect_coi_evidence(subcontractor)
            if item.validated
        ]

        if validated_evidence:
            coverage = validated_evidence[0].value.get("coverage")

            if coverage:
                return coverage

    return None


def _project_summary_view_model(project):
    status = project.mobilization_status
    label = _mobilization_label(status)

    return {
        "name": project.name,
        "status": label,
        "message": _mobilization_message(label),
        "risk": project.risk_level,
        "contract_value": _money_short(project.contract_value),
        "contract_value_full": _money_full(project.contract_value),
        "required_coverage": _coverage_label(project.required_coverage),
        "start_date": (
            project.start_date.strftime("%m/%d/%Y")
            if project.start_date
            else "Not scheduled"
        ),
        "end_date": (
            project.end_date.strftime("%m/%d/%Y")
            if project.end_date
            else "Not scheduled"
        ),
        "days_remaining": (
            f"{project.days_remaining} days"
            if project.days_remaining is not None
            else "Not scheduled"
        ),
    }


def _document_type_view(doc_type, docs):
    return {
        "type": doc_type,
        "documents": docs,
        "supports_analysis": supports_automatic_analysis(doc_type),
        "empty_message": f"No {doc_type} uploaded yet.",
    }


def _analyze_created_contract_documents(document_ids):
    for document_id in document_ids:
        try:
            analysis = analyze_and_save_document(document_id)
        except Exception as error:
            db.session.rollback()

            failed_doc = db.session.get(Document, document_id)

            if failed_doc:
                failed_doc.ai_status = "failed"
                failed_doc.ai_error = "Document analysis failed."
                db.session.commit()

            logger.error(
                "Automatic contract analysis failed document_id=%s error_type=%s",
                document_id,
                error.__class__.__name__,
            )

            return False

        if not analysis["success"]:
            logger.info(
                "Automatic contract analysis did not complete document_id=%s",
                document_id,
            )
            return False

    return True


def _cleanup_saved_documents(storage_keys):
    for storage_key in storage_keys:
        cleanup_saved_document(storage_key)


def _render_add_project(
    organization,
    subs,
    form_data=None,
    selected_subcontractor_ids=None,
):
    return render_template(
        "add_project.html",
        subs=subs,
        project_document_types=PROJECT_DOCUMENT_TYPES,
        capacity_usage=get_organization_usage(organization),
        coverage_options=PROJECT_REQUIRED_COVERAGE_OPTIONS,
        coverage_form=_coverage_form_values(form_data=form_data),
        form_data=form_data,
        selected_subcontractor_ids=selected_subcontractor_ids or set(),
    )


def _render_edit_project(
    project,
    subs,
    form_data=None,
    selected_subcontractor_ids=None,
):
    if selected_subcontractor_ids is None:
        selected_subcontractor_ids = {
            link.subcontractor_id
            for link in project.subs
        }

    return render_template(
        "edit_project.html",
        project=project,
        subs=subs,
        project_document_types=PROJECT_DOCUMENT_TYPES,
        coverage_options=PROJECT_REQUIRED_COVERAGE_OPTIONS,
        coverage_form=_coverage_form_values(
            getattr(project, "required_coverage", None),
            form_data=form_data,
        ),
        form_data=form_data,
        selected_subcontractor_ids=selected_subcontractor_ids,
    )


@projects_bp.route("/add_project", methods=["GET", "POST"])
@login_required
@subscription_required
def add_project():

    organization = get_current_organization()

    if not organization:
        abort(403)

    subs = scoped_subcontractor_query().all()

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        end_raw = request.form.get("end_date")
        selected_sub_ids = _selected_owned_subcontractor_ids()

        if not name:
            flash("Project name is required.", "danger")
            return _render_add_project(
                organization,
                subs,
                request.form,
                selected_sub_ids,
            )

        try:
            required_coverage = _parse_required_coverage(request.form)
        except ValueError:
            flash("Invalid minimum coverage amount.", "danger")
            return _render_add_project(
                organization,
                subs,
                request.form,
                selected_sub_ids,
            )

        end_date = None
        if end_raw:
            try:
                end_date = datetime.strptime(end_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("Invalid end date.", "danger")
                return _render_add_project(
                    organization,
                    subs,
                    request.form,
                    selected_sub_ids,
                )

        try:
            require_project_capacity(organization)
        except PlanCapacityError as error:
            flash(error.check.message, "warning")
            return redirect(url_for("projects.add_project"))

        project = Project(
            name=name,
            contract_value=0,
            user_id=current_user.id,
            organization_id=organization.id,
            start_date=None,
            end_date=end_date,
            required_coverage=required_coverage,
        )

        db.session.add(project)
        db.session.flush()

        _add_missing_project_links(
            project,
            selected_sub_ids,
        )

        files = request.files.getlist("documents")
        doc_type = normalize_project_document_type(
            request.form.get("doc_type")
        )
        next_versions = defaultdict(lambda: 1)
        saved_storage_keys = []
        created_contract_document_ids = []

        for file in files:

            if not file or file.filename == "":
                continue

            if not allowed_file(file.filename):
                flash(f"Invalid file type: {file.filename}", "danger")
                continue

            try:
                original_name = file.filename

                storage_key = save_document_file(
                    file,
                    original_name,
                    project_id=project.id,
                )
                saved_storage_keys.append(storage_key)

                doc = Document(
                    filename=storage_key,
                    original_name=original_name,
                    document_type=doc_type,
                    version=next_versions[doc_type],
                    project_id=project.id,
                    uploaded_by=current_user.id,
                )

                db.session.add(doc)
                db.session.flush()

                if supports_automatic_analysis(doc_type):
                    created_contract_document_ids.append(doc.id)

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

        if created_contract_document_ids:
            if _analyze_created_contract_documents(
                created_contract_document_ids
            ):
                flash(
                    "Project created and contract analyzed successfully.",
                    "success",
                )
            else:
                flash(
                    "Project created, but automatic contract analysis could not be completed. "
                    "You can retry from the project page.",
                    "warning",
                )
        else:
            flash("Project created successfully", "success")

        return redirect(
            url_for(
                "projects.view_project",
                project_id=project.id,
            )
        )

    return _render_add_project(organization, subs)


@projects_bp.route("/edit_project/<int:project_id>", methods=["GET", "POST"])
@login_required
@subscription_required
def edit_project(project_id):

    project = Project.query.filter_by(
        id=project_id,
    ).filter(
        project_scope_filter(Project)
    ).first_or_404()

    subs = scoped_subcontractor_query().all()

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        selected_sub_ids = _selected_owned_subcontractor_ids()

        if not name:
            flash("Project name is required.", "danger")
            return _render_edit_project(
                project,
                subs,
                request.form,
                selected_sub_ids,
            )

        value_raw = request.form.get("contract_value")

        try:
            contract_value = float(value_raw) if value_raw else 0
        except ValueError:
            flash("Invalid contract value.", "danger")
            return _render_edit_project(
                project,
                subs,
                request.form,
                selected_sub_ids,
            )

        try:
            required_coverage = _parse_required_coverage(request.form)
        except ValueError:
            flash("Invalid minimum coverage amount.", "danger")
            return _render_edit_project(
                project,
                subs,
                request.form,
                selected_sub_ids,
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
            return _render_edit_project(
                project,
                subs,
                request.form,
                selected_sub_ids,
            )

        if start_date and end_date and end_date < start_date:
            flash("End date cannot be before start date.", "danger")
            return _render_edit_project(
                project,
                subs,
                request.form,
                selected_sub_ids,
            )

        project.start_date = start_date
        project.end_date = end_date
        project.name = name
        project.contract_value = contract_value
        project.required_coverage = required_coverage

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
                )
                db.session.add(new_link)

        saved_storage_keys = []
        uploaded_contract_documents = []
        file = request.files.get("file")

        if file and file.filename != "":

            if allowed_file(file.filename):

                original_name = file.filename

                storage_key = save_document_file(
                    file,
                    original_name,
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
                    uploaded_by=current_user.id,
                )

                db.session.add(new_doc)

                if supports_automatic_analysis(doc_type):
                    uploaded_contract_documents.append(new_doc)

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

        uploaded_contract_document_ids = [
            doc.id
            for doc in uploaded_contract_documents
        ]

        if uploaded_contract_document_ids:
            if _analyze_created_contract_documents(
                uploaded_contract_document_ids
            ):
                flash("Project updated and contract analyzed successfully.", "success")
            else:
                flash(
                    "Project updated, but automatic contract analysis could not be completed. "
                    "You can retry from the project page.",
                    "warning",
                )
        else:
            flash("Project updated successfully!", "success")

        return redirect(
            url_for(
                "projects.view_project",
                project_id=project.id,
            )
        )

    return _render_edit_project(project, subs)


@projects_bp.route("/project/<int:project_id>")
@login_required
@subscription_required
def view_project(project_id):

    project = (
        scoped_project_query()
        .filter(Project.id == project_id)
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

    document_groups = [
        _document_type_view(
            doc_type,
            documents.get(doc_type, []),
        )
        for doc_type in PROJECT_DOCUMENT_TYPES
    ]

    return render_template(
        "view_project.html",
        project=project,
        project_summary=_project_summary_view_model(project),
        subcontractor_rows=subcontractor_rows,
        documents=dict(documents),
        document_groups=document_groups,
        project_document_types=PROJECT_DOCUMENT_TYPES,
        ai_summary=ai_summary,
    )


@projects_bp.route("/delete_project/<int:id>", methods=["POST"])
@login_required
@subscription_required
def delete_project(id):

    project = Project.query.filter_by(
        id=id,
    ).filter(
        project_scope_filter(Project)
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
@subscription_required
def upload_project_document(project_id):

    project = Project.query.filter_by(
        id=project_id,
    ).filter(
        project_scope_filter(Project)
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

    try:
        storage_key = save_document_file(
            file,
            original_name,
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
        uploaded_by=current_user.id,
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

    if supports_automatic_analysis(doc_type):
        if _analyze_created_contract_documents([new_doc.id]):
            flash("Document uploaded and contract analyzed successfully.", "success")
        else:
            flash(
                "Document uploaded, but automatic contract analysis could not be completed. "
                "You can retry from the project page.",
                "warning",
            )
    else:
        flash("Document uploaded successfully!", "success")

    return redirect(
        url_for(
            "projects.view_project",
            project_id=project.id,
        )
    )
