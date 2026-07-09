import os

from app.services.document_intelligence.document_router import (
    normalize_document_type,
)

from app.services.document_intelligence.openai_document_parser import (
    parse_document_with_ai,
)

from app.services.document_intelligence.validators import (
    validate_background_check_data,
    validate_coi_data,
    validate_drug_test_data,
    validate_license_data,
    validate_osha_data,
    validate_safety_training_data,
    validate_w9_data,
)


def get_mock_data_for_document(category):

    mock_data = {
        "w9": {
            "legal_name": "ABC Flooring LLC",
            "tax_id_last4": "1234",
            "confidence": 0.91,
        },
        "license": {
            "business_name": "ABC Flooring LLC",
            "license_number": "LIC-987654",
            "expiration_date": "2027-12-31",
            "confidence": 0.9,
        },
        "osha": {
            "holder_name": "John Smith",
            "training_type": "OSHA 10",
            "completion_date": "2026-02-15",
            "confidence": 0.88,
        },
        "drug_test": {
            "person_name": "John Smith",
            "result": "negative",
            "test_date": "2026-03-01",
            "confidence": 0.89,
        },
        "safety_training": {
            "person_name": "John Smith",
            "training_name": "Site Safety Orientation",
            "completion_date": "2026-03-10",
            "confidence": 0.9,
        },
        "background_check": {
            "person_name": "John Smith",
            "status": "clear",
            "completed_date": "2026-03-12",
            "confidence": 0.87,
        },
    }

    return mock_data.get(
        category,
        {
            "confidence": 0.0,
            "notes": "Unsupported document type.",
        }
    )


def validate_by_category(category, extracted_data):

    if category == "w9":
        return validate_w9_data(extracted_data)

    if category == "license":
        return validate_license_data(extracted_data)

    if category == "osha":
        return validate_osha_data(extracted_data)

    if category == "drug_test":
        return validate_drug_test_data(extracted_data)

    if category == "safety_training":
        return validate_safety_training_data(extracted_data)

    if category == "background_check":
        return validate_background_check_data(extracted_data)

    if category == "coi":
        return validate_coi_data(extracted_data)

    return None


def analyze_document_intelligence(
    file_path,
    document_type,
):

    category = normalize_document_type(
        document_type
    )

    if category == "other":
        return {
            "success": False,
            "error": f"Unsupported document type: {document_type}",
            "extracted_data": None,
            "compliance": None,
            "category": category,
        }

    mock_mode = os.getenv(
        "AI_MOCK_MODE",
        "false"
    ).lower() == "true"

    if mock_mode:
        extracted_data = get_mock_data_for_document(
            category
        )

    else:
        parse_result = parse_document_with_ai(
            file_path=file_path,
            category=category,
        )

        if not parse_result["success"]:
            return {
                "success": False,
                "error": parse_result["error"],
                "extracted_data": None,
                "compliance": None,
                "category": category,
            }

        extracted_data = parse_result["data"]

    validation = validate_by_category(
        category,
        extracted_data,
    )

    if not validation:
        return {
            "success": False,
            "error": f"No validator found for category: {category}",
            "extracted_data": extracted_data,
            "compliance": None,
            "category": category,
        }

    return {
        "success": True,
        "error": None,
        "extracted_data": extracted_data,
        "compliance": validation.to_dict(),
        "category": category,
    }