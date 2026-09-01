import hashlib
import html
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

from flask import current_app

from app.extensions import db
from app.models import (
    DOCUMENT_REQUEST_COMPLETED,
    DOCUMENT_REQUEST_EXPIRED,
    DOCUMENT_REQUEST_PENDING,
    DocumentRequest,
    Project,
    ProjectSubcontractor,
    Subcontractor,
)
from app.services.documents.types import SUBCONTRACTOR_DOCUMENT_TYPE
from app.services.compliance_evidence_service import collect_coi_evidence
from app.services.notifications.email_service import send_email_reminder


logger = logging.getLogger(__name__)


class DocumentRequestError(ValueError):
    pass


@dataclass(frozen=True)
class DocumentRequestDelivery:
    request: DocumentRequest
    token: str
    sent: bool


def hash_document_request_token(token):
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def generate_document_request_token():
    return secrets.token_urlsafe(32)


def create_or_resend_coi_request(
    *,
    organization,
    project,
    subcontractor,
    created_by_user_id,
):
    _validate_request_scope(organization, project, subcontractor)
    _validate_subcontractor_email(subcontractor)

    request = _active_pending_request(
        organization.id,
        project.id,
        subcontractor.id,
        SUBCONTRACTOR_DOCUMENT_TYPE,
    )

    token = generate_document_request_token()
    token_hash = hash_document_request_token(token)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_at = now + timedelta(
        days=current_app.config.get(
            "DOCUMENT_REQUEST_EXPIRATION_DAYS",
            7,
        )
    )

    if request:
        request.token_hash = token_hash
        request.expires_at = expires_at
    else:
        request = DocumentRequest(
            organization_id=organization.id,
            project_id=project.id,
            subcontractor_id=subcontractor.id,
            document_type=SUBCONTRACTOR_DOCUMENT_TYPE,
            token_hash=token_hash,
            expires_at=expires_at,
            status=DOCUMENT_REQUEST_PENDING,
            created_by_user_id=created_by_user_id,
        )
        db.session.add(request)

    db.session.flush()

    sent = send_document_request_email(request, token)

    if sent:
        request.sent_at = request.sent_at or now
        request.last_sent_at = now

    db.session.commit()

    return DocumentRequestDelivery(
        request=request,
        token=token,
        sent=sent,
    )


def get_valid_document_request(token):
    request = (
        DocumentRequest.query
        .filter_by(token_hash=hash_document_request_token(token))
        .first()
    )

    if not request:
        return None

    if request.status != DOCUMENT_REQUEST_PENDING:
        return None

    if _is_expired(request):
        request.status = DOCUMENT_REQUEST_EXPIRED
        db.session.commit()
        return None

    if not _request_relationships_valid(request):
        return None

    return request


def complete_document_request(request, document):
    request.status = DOCUMENT_REQUEST_COMPLETED
    request.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    request.document_id = document.id


def send_document_request_email(request, token):
    upload_url = document_request_url(token)
    correction_context = _coverage_correction_context(request)
    expires_in_days = current_app.config.get(
        "DOCUMENT_REQUEST_EXPIRATION_DAYS",
        7,
    )

    if correction_context:
        subject = (
            "Corrected Certificate of Insurance requested for "
            f"{request.project.name}"
        )
        message, html_message = _corrected_coi_email_content(
            organization_name=request.organization.name,
            project_name=request.project.name,
            upload_url=upload_url,
            expires_in_days=expires_in_days,
            current_coverage=correction_context["current"],
            required_coverage=correction_context["required"],
        )
    else:
        subject = (
            "Certificate of Insurance requested for "
            f"{request.project.name}"
        )
        message, html_message = _coi_request_email_content(
            organization_name=request.organization.name,
            project_name=request.project.name,
            upload_url=upload_url,
            expires_in_days=expires_in_days,
        )

    try:
        return send_email_reminder(
            request.subcontractor.email,
            subject,
            message,
            html_message=html_message,
        )
    except Exception as error:
        logger.error(
            "Document request email failed request_id=%s error_type=%s",
            request.id,
            error.__class__.__name__,
        )
        return False


def _coi_request_email_content(
    *,
    organization_name,
    project_name,
    upload_url,
    expires_in_days,
):
    text = (
        "BuildSure\n\n"
        "Certificate of Insurance Request\n\n"
        f"{organization_name} has requested an updated Certificate of "
        "Insurance.\n\n"
        "Project\n"
        f"{project_name}\n\n"
        "Upload COI:\n"
        f"{upload_url}\n\n"
        "No account required.\n"
        f"This secure upload link expires in {expires_in_days} days.\n\n"
        "If the button does not work, copy and paste this link into your "
        "browser:\n"
        f"{upload_url}\n\n"
        "Powered by BuildSure"
    )
    html_message = _document_request_email_html(
        heading="Certificate of Insurance Request",
        organization_copy=(
            f"{organization_name} has requested an updated Certificate of "
            "Insurance."
        ),
        project_name=project_name,
        upload_url=upload_url,
        cta_label="Upload COI",
        expires_in_days=expires_in_days,
    )

    return text, html_message


def _corrected_coi_email_content(
    *,
    organization_name,
    project_name,
    upload_url,
    expires_in_days,
    current_coverage,
    required_coverage,
):
    current_label = _money_label(current_coverage)
    required_label = _money_label(required_coverage)
    text = (
        "BuildSure\n\n"
        "Corrected Certificate of Insurance Requested\n\n"
        f"{organization_name} has requested a corrected Certificate of "
        "Insurance.\n\n"
        "Project\n"
        f"{project_name}\n\n"
        "Current General Liability\n"
        f"{current_label}\n\n"
        "Required General Liability\n"
        f"{required_label}\n\n"
        "Upload Corrected COI:\n"
        f"{upload_url}\n\n"
        "No account required.\n"
        f"This secure upload link expires in {expires_in_days} days.\n\n"
        "If the button does not work, copy and paste this link into your "
        "browser:\n"
        f"{upload_url}\n\n"
        "Powered by BuildSure"
    )
    html_message = _document_request_email_html(
        heading="Corrected Certificate of Insurance Requested",
        organization_copy=(
            f"{organization_name} has requested a corrected Certificate of "
            "Insurance."
        ),
        project_name=project_name,
        upload_url=upload_url,
        cta_label="Upload Corrected COI",
        expires_in_days=expires_in_days,
        facts=[
            ("Current General Liability", current_label),
            ("Required General Liability", required_label),
        ],
    )

    return text, html_message


def _document_request_email_html(
    *,
    heading,
    organization_copy,
    project_name,
    upload_url,
    cta_label,
    expires_in_days,
    facts=None,
):
    escaped_heading = html.escape(heading)
    escaped_copy = html.escape(organization_copy)
    escaped_project = html.escape(project_name)
    escaped_url = html.escape(upload_url, quote=True)
    escaped_url_text = html.escape(upload_url)
    escaped_cta = html.escape(cta_label)
    fact_markup = ""

    for label, value in facts or []:
        fact_markup += (
            "<p style=\"margin:0 0 12px;\">"
            f"<span style=\"display:block;color:#64748b;font-size:12px;"
            f"font-weight:700;text-transform:uppercase;\">{html.escape(label)}</span>"
            f"<span style=\"color:#0f172a;font-size:16px;font-weight:700;\">"
            f"{html.escape(value)}</span>"
            "</p>"
        )

    return (
        "<!doctype html>"
        "<html>"
        "<body style=\"margin:0;padding:0;background:#f8fafc;"
        "font-family:Arial,sans-serif;color:#0f172a;\">"
        "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" "
        "cellpadding=\"0\" style=\"background:#f8fafc;padding:24px;\">"
        "<tr><td align=\"center\">"
        "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" "
        "cellpadding=\"0\" style=\"max-width:560px;background:#ffffff;"
        "border:1px solid #e2e8f0;border-radius:10px;padding:32px;\">"
        "<tr><td>"
        "<p style=\"margin:0 0 18px;color:#475569;font-size:14px;"
        "font-weight:700;letter-spacing:.02em;\">BuildSure</p>"
        f"<h1 style=\"margin:0 0 18px;font-size:24px;line-height:1.25;"
        f"color:#0f172a;\">{escaped_heading}</h1>"
        f"<p style=\"margin:0 0 22px;color:#475569;font-size:16px;"
        f"line-height:1.5;\">{escaped_copy}</p>"
        "<p style=\"margin:0 0 12px;\">"
        "<span style=\"display:block;color:#64748b;font-size:12px;"
        "font-weight:700;text-transform:uppercase;\">Project</span>"
        f"<span style=\"color:#0f172a;font-size:16px;font-weight:700;\">"
        f"{escaped_project}</span>"
        "</p>"
        f"{fact_markup}"
        f"<p style=\"margin:24px 0;\"><a href=\"{escaped_url}\" "
        "style=\"display:inline-block;background:#2563eb;color:#ffffff;"
        "text-decoration:none;border-radius:7px;padding:12px 18px;"
        f"font-weight:700;\">{escaped_cta}</a></p>"
        "<p style=\"margin:0 0 8px;color:#475569;font-size:14px;\">"
        "No account required.</p>"
        f"<p style=\"margin:0 0 20px;color:#475569;font-size:14px;\">"
        f"This secure upload link expires in {int(expires_in_days)} days.</p>"
        "<p style=\"margin:0 0 8px;color:#475569;font-size:14px;\">"
        "If the button does not work, copy and paste this link into your "
        "browser:</p>"
        f"<p style=\"margin:0 0 24px;color:#2563eb;font-size:14px;"
        f"line-height:1.5;word-break:break-all;\">{escaped_url_text}</p>"
        "<p style=\"margin:0;color:#64748b;font-size:13px;\">"
        "Powered by BuildSure</p>"
        "</td></tr></table>"
        "</td></tr></table>"
        "</body>"
        "</html>"
    )


def _coverage_correction_context(request):
    required_coverage = getattr(
        request.project,
        "required_coverage",
        None,
    )

    if not required_coverage:
        return None

    validated_evidence = [
        evidence
        for evidence in collect_coi_evidence(request.subcontractor)
        if evidence.validated
    ]

    if not validated_evidence:
        return None

    current_coverage = validated_evidence[0].value.get("coverage")

    try:
        current = int(float(current_coverage))
        required = int(float(required_coverage))
    except (TypeError, ValueError):
        return None

    if current >= required:
        return None

    return {
        "current": current,
        "required": required,
    }


def _money_label(value):
    amount = int(value or 0)

    if amount >= 1_000_000 and amount % 1_000_000 == 0:
        return f"${amount // 1_000_000}M"

    return f"${amount:,.0f}"


def document_request_url(token):
    base_url = current_app.config.get("APPLICATION_BASE_URL")

    if not base_url:
        raise DocumentRequestError("APPLICATION_BASE_URL is required.")

    path = f"/document-request/{token}"
    return urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))


def _active_pending_request(
    organization_id,
    project_id,
    subcontractor_id,
    document_type,
):
    requests = (
        DocumentRequest.query
        .filter_by(
            organization_id=organization_id,
            project_id=project_id,
            subcontractor_id=subcontractor_id,
            document_type=document_type,
            status=DOCUMENT_REQUEST_PENDING,
        )
        .order_by(DocumentRequest.created_at.desc())
        .all()
    )

    for request in requests:
        if _is_expired(request):
            request.status = DOCUMENT_REQUEST_EXPIRED
            continue

        return request

    return None


def _validate_request_scope(organization, project, subcontractor):
    if not organization or not project or not subcontractor:
        raise DocumentRequestError("Request scope is incomplete.")

    if project.organization_id != organization.id:
        raise DocumentRequestError("Project does not belong to this organization.")

    if subcontractor.organization_id != organization.id:
        raise DocumentRequestError(
            "Subcontractor does not belong to this organization."
        )

    link = (
        ProjectSubcontractor.query
        .filter_by(
            project_id=project.id,
            subcontractor_id=subcontractor.id,
        )
        .first()
    )

    if not link:
        raise DocumentRequestError(
            "Subcontractor is not linked to this project."
        )


def _validate_subcontractor_email(subcontractor):
    email = (subcontractor.email or "").strip()

    if "@" not in email:
        raise DocumentRequestError("Subcontractor email is required.")


def _request_relationships_valid(request):
    project = db.session.get(Project, request.project_id)
    subcontractor = db.session.get(Subcontractor, request.subcontractor_id)

    if not project or not subcontractor:
        return False

    if project.organization_id != request.organization_id:
        return False

    if subcontractor.organization_id != request.organization_id:
        return False

    return (
        ProjectSubcontractor.query
        .filter_by(
            project_id=project.id,
            subcontractor_id=subcontractor.id,
        )
        .first()
        is not None
    )


def _is_expired(request):
    expires_at = request.expires_at

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    return expires_at <= datetime.now(timezone.utc)
