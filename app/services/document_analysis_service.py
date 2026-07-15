import logging

from datetime import datetime

from app.extensions import db

from app.models import Document, Subcontractor

from app.services.documents.storage import temporary_document_path

from app.services.compliance_evidence_service import (
    coi_evidence_from_document,
)

from app.services.document_intelligence import (
    analyze_document_intelligence,
)

from app.services.subcontractor_compliance_service import (
    update_subcontractor_compliance,
)


logger = logging.getLogger(__name__)


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

    with temporary_document_path(doc) as file_path:
        if not file_path:
            return {
                "success": False,
                "error": "Document file not found.",
                "document": doc,
                "result": None,
            }

        result = analyze_document_intelligence(
            file_path=file_path,
            document_type=doc.document_type,
        )

    if not result["success"]:

        doc.ai_status = "failed"
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

    if subcontractor.coi_expiration:
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
