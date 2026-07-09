from app.services.ai.coi_parser_service import parse_coi

from app.services.compliance.decision_engine import (
    evaluate_document_compliance,
)


def analyze_coi_document(
    file_path,
    trade=None,
    profile=None,
    project_end_date=None,
):

    parse_result = parse_coi(file_path)

    if not parse_result["success"]:
        return {
            "success": False,
            "error": parse_result["error"],
            "coi_data": None,
            "compliance": None,
        }

    coi_data = parse_result["data"]

    compliance = evaluate_document_compliance(
        coi_data=coi_data,
        trade=trade,
        profile=profile,
        project_end_date=project_end_date,
    )

    return {
        "success": True,
        "error": None,
        "coi_data": coi_data,
        "compliance": compliance,
    }