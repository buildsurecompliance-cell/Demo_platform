from app.services.document_intelligence import DocumentIntelligenceResult


def validate_license_data(data):

    issues = []
    warnings = []
    score = 100

    if not data.get("license_number"):
        issues.append({"field": "license_number", "message": "License number missing."})
        score -= 30

    if not data.get("expiration_date"):
        warnings.append({"field": "expiration_date", "message": "License expiration date not found."})
        score -= 15

    status = "Blocked" if issues else "Pending" if warnings else "Ready"

    return DocumentIntelligenceResult(
        document_type="Business License",
        status=status,
        score=max(score, 0),
        extracted_data=data,
        issues=issues,
        warnings=warnings,
        confidence=data.get("confidence", 0.0),
        summary="License validation completed.",
    )