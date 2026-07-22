import logging
import os
import hashlib
import re

from datetime import datetime

from app.extensions import db

from app.models import Document, Subcontractor

from app.services.documents.storage import temporary_document_path
from app.services.documents.storage import (
    document_storage_keys,
    resolve_document_storage_key,
)

from app.services.compliance_evidence_service import (
    coi_evidence_from_document,
    collect_coi_evidence,
)

from app.services.document_intelligence import (
    analyze_document_intelligence,
)

from app.services.projects.contract_autofill_service import (
    apply_contract_extraction_to_project,
)

from app.services.subcontractor_compliance_service import (
    update_subcontractor_compliance,
)


logger = logging.getLogger(__name__)


def _result_keys(result):
    extracted_data = result.get("extracted_data") or {}

    if not isinstance(extracted_data, dict):
        return []

    return sorted(extracted_data.keys())


def reset_document_analysis(doc, status="not_analyzed"):
    doc.ai_status = status
    doc.ai_confidence = None
    doc.ai_extracted_data = None
    doc.ai_compliance_result = None
    doc.ai_error = None
    doc.ai_analyzed_at = None


def file_sha256(file_path):
    digest = hashlib.sha256()

    with open(file_path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def analyze_and_save_document(doc_id):

    doc = db.session.get(
        Document,
        doc_id
    )

    if not doc:
        return {
            "success": False,
            "error": "Document not found.",
            "document": None,
            "result": None,
        }

    storage_candidates = document_storage_keys(doc)
    resolved_storage_key = resolve_document_storage_key(doc)

    logger.info(
        "Document analysis storage candidates document_id=%s filename=%s resolved_key=%s candidates=%s",
        doc.id,
        getattr(doc, "filename", None),
        resolved_storage_key,
        storage_candidates,
    )

    with temporary_document_path(doc) as file_path:
        if not file_path:
            return {
                "success": False,
                "error": "Document file not found.",
                "document": doc,
                "result": None,
            }

        file_size = os.path.getsize(file_path)
        sha256 = file_sha256(file_path)

        logger.info(
            "Document analysis started document_id=%s original_name=%s filename=%s resolved_key=%s temp_path=%s size=%s sha256=%s document_type=%s",
            doc.id,
            doc.original_name,
            getattr(doc, "filename", None),
            resolved_storage_key,
            file_path,
            file_size,
            sha256,
            doc.document_type,
        )

        result = analyze_document_intelligence(
            file_path=file_path,
            document_type=doc.document_type,
        )

        logger.info(
            "Document analysis completed document_id=%s original_name=%s success=%s category=%s extracted_keys=%s",
            doc.id,
            doc.original_name,
            result.get("success"),
            result.get("category"),
            _result_keys(result),
        )

    if not result["success"]:

        reset_document_analysis(doc, "failed")
        doc.ai_error = result["error"]
        doc.ai_analyzed_at = datetime.utcnow()

        db.session.commit()

        return {
            "success": False,
            "error": result["error"],
            "document": doc,
            "result": result,
        }

    extracted_data = result["extracted_data"]
    compliance = result["compliance"]

    doc.ai_status = "analyzed"
    doc.ai_confidence = extracted_data.get("confidence")
    doc.ai_extracted_data = extracted_data
    doc.ai_compliance_result = compliance
    doc.ai_error = None
    doc.ai_analyzed_at = datetime.utcnow()

    _auto_fill_subcontractor_coi_expiration(doc)
    _auto_fill_subcontractor_trade(doc)
    _auto_fill_subcontractor_contact(doc)
    _auto_fill_project_contract_fields(doc)

    db.session.commit()

    if doc.sub_id:
        update_subcontractor_compliance(
            doc.sub_id
        )

    return {
        "success": True,
        "error": None,
        "document": doc,
        "result": {
            "success": True,
            "error": None,
            "coi_data": extracted_data,
            "compliance": compliance,
            "category": result.get("category"),
        },
    }


def _auto_fill_subcontractor_coi_expiration(doc):
    if not doc.sub_id:
        return False

    evidence = coi_evidence_from_document(doc)

    if not evidence.validated:
        logger.info(
            "COI expiration auto-fill skipped document_id=%s reason=%s",
            doc.id,
            evidence.rejection_code,
        )
        return False

    expiration_date = evidence.value.get(
        "expiration_date"
    )

    if not expiration_date:
        logger.info(
            "COI expiration auto-fill skipped document_id=%s reason=missing_expiration",
            doc.id,
        )
        return False

    subcontractor = db.session.get(
        Subcontractor,
        doc.sub_id,
    )

    if not subcontractor:
        logger.info(
            "COI expiration auto-fill skipped document_id=%s reason=subcontractor_missing",
            doc.id,
        )
        return False

    if subcontractor.coi_expiration and not _matches_prior_auto_filled_expiration(
        doc,
        subcontractor.coi_expiration,
    ):
        logger.info(
            "COI expiration auto-fill skipped document_id=%s subcontractor_id=%s reason=manual_value_present",
            doc.id,
            subcontractor.id,
        )
        return False

    subcontractor.coi_expiration = expiration_date

    logger.info(
        "COI expiration auto-filled document_id=%s subcontractor_id=%s",
        doc.id,
        subcontractor.id,
    )

    return True


def _matches_prior_auto_filled_expiration(doc, current_expiration):
    if not doc.sub_id or not current_expiration:
        return False

    subcontractor = db.session.get(
        Subcontractor,
        doc.sub_id,
    )

    if not subcontractor:
        return False

    for evidence in collect_coi_evidence(subcontractor):
        if not evidence.validated or evidence.document_id == doc.id:
            continue

        if evidence.value.get("expiration_date") == current_expiration:
            return True

    return False


def _auto_fill_subcontractor_trade(doc):
    if not doc.sub_id:
        return False

    extracted = doc.ai_extracted_data or {}
    trade = (
        extracted.get("trade")
        or extracted.get("operations")
        or extracted.get("description_of_operations")
        or extracted.get("scope_of_work")
    )

    if not trade or not str(trade).strip():
        return False

    subcontractor = db.session.get(
        Subcontractor,
        doc.sub_id,
    )

    if not subcontractor:
        return False

    if subcontractor.role:
        logger.info(
            "Subcontractor trade auto-fill skipped document_id=%s subcontractor_id=%s reason=manual_value_present",
            doc.id,
            subcontractor.id,
        )
        return False

    subcontractor.role = str(trade).strip()

    logger.info(
        "Subcontractor trade auto-filled document_id=%s subcontractor_id=%s",
        doc.id,
        subcontractor.id,
    )

    return True


def _get_extracted_email(extracted_data):
    if not isinstance(extracted_data, dict):
        return None

    for key in (
        "email",
        "contact_email",
        "email_address",
        "insured_email",
        "subcontractor_email",
    ):
        email = _normalize_email(
            extracted_data.get(key)
        )

        if email:
            return email

    return None


def _get_extracted_phone(extracted_data):
    if not isinstance(extracted_data, dict):
        return None

    for key in (
        "phone",
        "phone_number",
        "contact_phone",
        "insured_phone",
        "subcontractor_phone",
        "telephone",
    ):
        phone = _normalize_phone(
            extracted_data.get(key)
        )

        if phone:
            return phone

    return None


def _normalize_email(value):
    if not value:
        return None

    match = re.search(
        r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}",
        str(value).strip(),
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    email = match.group(0).strip().strip(".,;:").lower()

    if not re.fullmatch(
        r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}",
        email,
    ):
        return None

    return email


def _normalize_phone(value):
    if not value:
        return None

    text = re.sub(
        r"(?:ext\.?|extension|x)\s*\d+\b",
        "",
        str(value).strip(),
        flags=re.IGNORECASE,
    )
    has_plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)

    if len(digits) == 10:
        return f"+1{digits}"

    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"

    if has_plus and len(digits) >= 11:
        return f"+{digits}"

    return None


def _auto_fill_subcontractor_contact(doc):
    if not doc.sub_id:
        return False

    extracted = doc.ai_extracted_data or {}

    if not isinstance(extracted, dict):
        logger.info(
            "Subcontractor contact auto-fill skipped document_id=%s reason=invalid_extracted_data",
            doc.id,
        )
        return False

    subcontractor = db.session.get(
        Subcontractor,
        doc.sub_id,
    )

    if not subcontractor:
        logger.info(
            "Subcontractor contact auto-fill skipped document_id=%s reason=subcontractor_missing",
            doc.id,
        )
        return False

    changed = False
    extracted_email = _get_extracted_email(extracted)
    extracted_phone = _get_extracted_phone(extracted)

    if extracted_email and not str(subcontractor.email or "").strip():
        subcontractor.email = extracted_email
        changed = True
        logger.info(
            "Subcontractor email auto-filled document_id=%s subcontractor_id=%s",
            doc.id,
            subcontractor.id,
        )
    elif extracted_email:
        logger.info(
            "Subcontractor email auto-fill skipped document_id=%s subcontractor_id=%s reason=manual_value_present",
            doc.id,
            subcontractor.id,
        )
    else:
        logger.info(
            "Subcontractor email auto-fill skipped document_id=%s subcontractor_id=%s reason=missing_or_invalid_email",
            doc.id,
            subcontractor.id,
        )

    if extracted_phone and not str(subcontractor.phone or "").strip():
        subcontractor.phone = extracted_phone
        changed = True
        logger.info(
            "Subcontractor phone auto-filled document_id=%s subcontractor_id=%s",
            doc.id,
            subcontractor.id,
        )
    elif extracted_phone:
        logger.info(
            "Subcontractor phone auto-fill skipped document_id=%s subcontractor_id=%s reason=manual_value_present",
            doc.id,
            subcontractor.id,
        )
    else:
        logger.info(
            "Subcontractor phone auto-fill skipped document_id=%s subcontractor_id=%s reason=missing_or_invalid_phone",
            doc.id,
            subcontractor.id,
        )

    return changed


def refresh_subcontractor_coi_from_evidence(sub_id, removed_expiration=None):
    subcontractor = db.session.get(
        Subcontractor,
        sub_id,
    )

    if not subcontractor:
        return False

    db.session.expire(
        subcontractor,
        ["documents"],
    )

    validated_evidence = [
        item
        for item in collect_coi_evidence(subcontractor)
        if item.validated
    ]

    if validated_evidence:
        expiration = validated_evidence[0].value.get("expiration_date")

        if expiration and subcontractor.coi_expiration != expiration:
            subcontractor.coi_expiration = expiration
            logger.info(
                "COI expiration refreshed from active evidence subcontractor_id=%s document_id=%s",
                subcontractor.id,
                validated_evidence[0].document_id,
            )
            return True

        return False

    if removed_expiration and subcontractor.coi_expiration == removed_expiration:
        subcontractor.coi_expiration = None
        logger.info(
            "COI expiration cleared after evidence removal subcontractor_id=%s",
            subcontractor.id,
        )
        return True

    return False


def _auto_fill_project_contract_fields(doc):
    try:
        return apply_contract_extraction_to_project(doc)
    except Exception:
        logger.exception(
            "Contract auto-fill failed document_id=%s",
            getattr(doc, "id", None),
        )
        raise
