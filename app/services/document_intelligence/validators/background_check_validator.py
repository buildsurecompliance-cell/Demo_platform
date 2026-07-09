from app.services.document_intelligence import DocumentIntelligenceResult


def validate_background_check_data(data):

    issues = []
    warnings = []
    score = 100

    if not data.get("person_name"):
        issues.append({"field": "person_name", "message": "Person name missing."})
        score -= 30

    if data.get("status") not in ["clear", "passed"]:
        issues.append({"field": "status", "message": "Background check is not confirmed clear."})
        score -= 50

    status = "Blocked" if issues else "Ready"

    return DocumentIntelligenceResult(
        document_type="Background Check",
        status=status,
        score=max(score, 0),
        extracted_data=data,
        issues=issues,
        warnings=warnings,
        confidence=data.get("confidence", 0.0),
        summary="Background check validation completed.",
    )