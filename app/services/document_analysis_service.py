import os

from datetime import datetime

from flask import current_app

from app.extensions import db

from app.models import Document

from app.services.document_intelligence import (
    analyze_document_intelligence,
)

from app.services.subcontractor_compliance_service import (
    update_subcontractor_compliance,
)


def resolve_document_path(doc):

    root_path = os.path.join(
        current_app.config["UPLOAD_FOLDER"],
        doc.filename,
    )

    if os.path.exists(root_path):
        return root_path

    if doc.project_id:

        project_path = os.path.join(
            current_app.config["UPLOAD_FOLDER"],
            f"project_{doc.project_id}",
            doc.filename,
        )

        if os.path.exists(project_path):
            return project_path

    return root_path


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

    file_path = resolve_document_path(doc)

    if not os.path.exists(file_path):
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