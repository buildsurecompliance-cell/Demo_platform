import hashlib
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

    if correction_context:
        subject = f"Corrected COI requested for {request.project.name}"
        message = (
            f"{request.organization.name} needs a corrected Certificate of "
            f"Insurance for {request.project.name}.\n\n"
            "The submitted COI shows:\n"
            f"General Liability: {_money_label(correction_context['current'])}\n\n"
            "Project requirement:\n"
            f"General Liability: {_money_label(correction_context['required'])}\n\n"
            "Please upload a corrected COI using the secure link below.\n\n"
            f"{upload_url}\n\n"
            "No account is required."
        )
    else:
        subject = f"COI requested for {request.project.name}"
        message = (
            f"{request.organization.name} needs an updated Certificate of "
            f"Insurance for {request.project.name}.\n\n"
            "Upload your COI using the secure link below.\n\n"
            f"{upload_url}\n\n"
            "No account is required."
        )

    try:
        return send_email_reminder(
            request.subcontractor.email,
            subject,
            message,
        )
    except Exception as error:
        logger.error(
            "Document request email failed request_id=%s error_type=%s",
            request.id,
            error.__class__.__name__,
        )
        return False


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
    base_url = current_app.config.get(
        "APPLICATION_BASE_URL",
        "http://localhost:8000",
    )
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
