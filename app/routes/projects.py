from collections import defaultdict
from datetime import datetime
import logging
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

from sqlalchemy.orm import joinedload, selectinload

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
from app.services.dashboard.project_readiness import (
    dashboard_readiness_for_project,
    is_processing_readiness,
)
from app.services.dashboard.readiness_presentation import (
    primary_issue_label,
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


def _project_list_schedule_label(project):
    if project.days_remaining is None:
        return "Not scheduled"

    if project.days_remaining == 0:
        return "Expired"

    return f"{project.days_remaining} days left"


def _project_list_row(project):
    return {
        "project": project,
        "name": project.name,
        "required_coverage": _coverage_label(project.required_coverage),
        "schedule": _project_list_schedule_label(project),
        "subcontractor_count": len(project.subs),
        "readiness": dashboard_readiness_for_project(project),
    }


def _mobilization_label(status):
    if status == "Ready to Mobilize":
        return "READY"

    if status == "Pending Compliance":
        return "PENDING"

    return "BLOCKED"


def _mobilization_message(label):
    messages = {
        "READY": "This project is ready to mobilize.",
        "BLOCKED": "One or more subcontractors cannot work today.",
        "CHECKING": "BuildSure is checking subcontractor documents.",
        "NO SUBCONTRACTORS": "No subcontractors assigned.",
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
    readiness = project_subcontractor.readiness
    status = _operational_subcontractor_status(readiness)
    evidence = _validated_coi_evidence(project_subcontractor.subcontractor)
    latest_request = _latest_document_request(project_subcontractor)
    issue = _project_subcontractor_issue(
        project_subcontractor,
        readiness,
        evidence,
        latest_request,
    )
    action = _project_subcontractor_action(
        project_subcontractor,
        readiness,
        status,
        latest_request,
    )

    return {
        "project_subcontractor": project_subcontractor,
        "status": status,
        "issue": issue,
        "document_request_status": _document_request_status_label(
            latest_request
        ),
        "action": action,
        "coi_expiration": (
            _date_with_year_label(
                _coi_expiration_for_project_subcontractor(
                    project_subcontractor
                )
            )
            or "Not available"
        ),
        "sort": _project_subcontractor_sort(status, latest_request, issue),
    }


def _operational_subcontractor_status(readiness):
    if readiness["status"] == "READY":
        return "READY"

    if is_processing_readiness(readiness):
        return "CHECKING"

    return "BLOCKED"


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


def _project_subcontractor_issue(
    project_subcontractor,
    readiness,
    evidence,
    latest_request,
):
    if (
        latest_request
        and latest_request.status == DOCUMENT_REQUEST_PENDING
    ):
        return "Waiting on subcontractor"

    codes = {
        reason.get("code")
        for reason in readiness.get("reasons", [])
    }

    if (
        "COI_DOCUMENT_UNREADABLE" in codes
        or "COI_LOW_CONFIDENCE" in codes
        or "COI_VALIDATOR_FAILED" in codes
        or "AI_CONFIDENCE_LOW" in codes
        or "AI_VALIDATION_FAILED" in codes
    ):
        return "COI analysis needs review"

    if is_processing_readiness(readiness):
        return "BuildSure is reviewing the latest COI."

    if readiness["status"] == "READY":
        expiration = _coi_expiration_for_project_subcontractor(
            project_subcontractor
        )
        if expiration:
            return f"COI expires {_date_with_year_label(expiration)}"

        return "Ready to work"

    return primary_issue_label(
        readiness,
        project_subcontractor.project,
        project_subcontractor.subcontractor,
        evidence,
        coverage_label=_coverage_label,
    )


def _project_subcontractor_action(
    project_subcontractor,
    readiness,
    status,
    latest_request,
):
    sub = project_subcontractor.subcontractor

    if status == "READY":
        return _link_action(
            "View Documents",
            "subcontractors.view_sub_documents",
            {"sub_id": sub.id},
        )

    if status == "CHECKING":
        return None

    codes = {
        reason.get("code")
        for reason in readiness.get("reasons", [])
    }

    if (
        "COI_DOCUMENT_UNREADABLE" in codes
        or "COI_DOCUMENT_PARTIAL" in codes
        or "COI_LOW_CONFIDENCE" in codes
        or "COI_VALIDATOR_FAILED" in codes
        or "AI_CONFIDENCE_LOW" in codes
        or "AI_VALIDATION_FAILED" in codes
    ):
        return _link_action(
            "Review Documents",
            "subcontractors.view_sub_documents",
            {"sub_id": sub.id},
        )

    if not (sub.email or "").strip():
        return _link_action(
            "Add Email",
            "subcontractors.edit_sub",
            {"id": sub.id},
        )

    if (
        latest_request
        and latest_request.status == DOCUMENT_REQUEST_PENDING
    ):
        return _post_action(
            "Resend",
            "document_requests.request_coi",
            {
                "project_id": project_subcontractor.project_id,
                "subcontractor_id": project_subcontractor.subcontractor_id,
            },
        )

    if (
        latest_request
        and latest_request.status == DOCUMENT_REQUEST_COMPLETED
    ):
        return _post_action(
            "Request Corrected COI",
            "document_requests.request_coi",
            {
                "project_id": project_subcontractor.project_id,
                "subcontractor_id": project_subcontractor.subcontractor_id,
            },
        )

    if "COVERAGE_INSUFFICIENT" in codes:
        return _post_action(
            "Request Corrected COI",
            "document_requests.request_coi",
            {
                "project_id": project_subcontractor.project_id,
                "subcontractor_id": project_subcontractor.subcontractor_id,
            },
        )

    return _post_action(
        "Request COI",
        "document_requests.request_coi",
        {
            "project_id": project_subcontractor.project_id,
            "subcontractor_id": project_subcontractor.subcontractor_id,
        },
    )


def _link_action(label, endpoint, params):
    return {
        "kind": "link",
        "label": label,
        "endpoint": endpoint,
        "params": params,
    }


def _post_action(label, endpoint, params):
    return {
        "kind": "post",
        "label": label,
        "endpoint": endpoint,
        "params": params,
    }


def _project_subcontractor_sort(status, latest_request, issue):
    if status == "BLOCKED" and not (
        latest_request
        and latest_request.status == DOCUMENT_REQUEST_PENDING
    ):
        return 0

    if status == "BLOCKED":
        return 1

    if status == "CHECKING":
        return 2

    return 3


def _short_date_label(value):
    if not value:
        return ""

    if isinstance(value, datetime):
        value = value.date()

    if isinstance(value, str):
        return value

    return value.strftime("%b %d").replace(" 0", " ")


def _date_with_year_label(value):
    if not value:
        return ""

    if isinstance(value, datetime):
        value = value.date()

    if isinstance(value, str):
        return value

    return value.strftime("%b %d, %Y").replace(" 0", " ")


def _validated_coi_evidence(subcontractor):
    if not subcontractor:
        return None

    for evidence in collect_coi_evidence(subcontractor):
        if evidence.validated:
            return evidence

    return None


def _coi_expiration_for_project_subcontractor(project_subcontractor):
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
            expiration = validated_evidence[0].value.get("expiration_date")

            if expiration:
                return expiration

        return getattr(
            subcontractor,
            "coi_expiration",
            None,
        )

    return None


def _project_summary_view_model(project):
    label = dashboard_readiness_for_project(project)

    if label == "NEEDS ATTENTION":
        label = "BLOCKED"

    return {
        "name": project.name,
        "status": label,
        "message": _mobilization_message(label),
        "required_coverage": _coverage_label(project.required_coverage),
        "end_date": (
            project.end_date.strftime("%m/%d/%Y")
            if project.end_date
            else "Not scheduled"
        ),
        "subcontractor_count": len(project.subs),
    }


def _document_view_model(doc):
    return {
        "document": doc,
        "status": _project_document_status_label(doc),
    }


def _project_document_status_label(doc):
    if not supports_automatic_analysis(doc.document_type):
        return "UPLOADED"

    if doc.ai_status == "analyzed":
        return "ANALYZED"

    if doc.ai_status == "failed":
        return "FAILED"

    return "PROCESSING"


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

    project_documents = (
        Document.query
        .filter_by(project_id=project.id)
        .order_by(Document.document_type.asc(), Document.version.desc())
        .all()
    )
    project_document_types_on_file = []
    for document in project_documents:
        if document.document_type not in project_document_types_on_file:
            project_document_types_on_file.append(document.document_type)

    return render_template(
        "edit_project.html",
        project=project,
        subs=subs,
        coverage_options=PROJECT_REQUIRED_COVERAGE_OPTIONS,
        coverage_form=_coverage_form_values(
            getattr(project, "required_coverage", None),
            form_data=form_data,
        ),
        form_data=form_data,
        selected_subcontractor_ids=selected_subcontractor_ids,
        project_document_count=len(project_documents),
        project_document_types_on_file=project_document_types_on_file,
    )


@projects_bp.route("/projects")
@login_required
@subscription_required
def list_projects():
    search = request.args.get(
        "search",
        "",
    ).strip()

    query = scoped_project_query().options(
        selectinload(Project.subs)
        .selectinload(ProjectSubcontractor.subcontractor),
    )

    if search:
        query = query.filter(
            Project.name.ilike(f"%{search}%")
        )

    projects = query.order_by(
        Project.id.desc()
    ).all()

    return render_template(
        "projects_list.html",
        project_rows=[
            _project_list_row(project)
            for project in projects
        ],
        search=search,
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

        end_raw = request.form.get("end_date")

        try:
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

        if project.start_date and end_date and end_date < project.start_date:
            flash("End date cannot be before start date.", "danger")
            return _render_edit_project(
                project,
                subs,
                request.form,
                selected_sub_ids,
            )

        project.end_date = end_date
        project.name = name
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

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
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

    links = (
        ProjectSubcontractor.query
        .options(joinedload(ProjectSubcontractor.subcontractor))
        .filter_by(project_id=project.id)
        .all()
    )

    subcontractor_rows = sorted(
        [
            _project_subcontractor_view_model(link)
            for link in links
        ],
        key=lambda row: (
            row["sort"],
            row["project_subcontractor"].subcontractor.name.lower(),
        ),
    )

    ready_count = sum(
        1
        for row in subcontractor_rows
        if row["status"] == "READY"
    )
    blocked_count = sum(
        1
        for row in subcontractor_rows
        if row["status"] == "BLOCKED"
    )
    checking_count = sum(
        1
        for row in subcontractor_rows
        if row["status"] == "CHECKING"
    )

    document_rows = [
        _document_view_model(doc)
        for doc in (
            Document.query
            .filter_by(project_id=project.id)
            .order_by(Document.uploaded_at.desc())
            .all()
        )
    ]

    return render_template(
        "view_project.html",
        project=project,
        project_summary=_project_summary_view_model(project),
        subcontractor_rows=subcontractor_rows,
        ready_count=ready_count,
        blocked_count=blocked_count,
        checking_count=checking_count,
        document_rows=document_rows,
        project_document_types=PROJECT_DOCUMENT_TYPES,
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
