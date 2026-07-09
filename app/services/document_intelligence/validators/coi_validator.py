from app.services.document_intelligence import DocumentIntelligenceResult


def validate_coi_data(data):

    issues = []
    warnings = []
    score = 100

    if not data.get("expiration_date"):
        issues.append({"field": "expiration_date", "message": "Expiration date missing."})
        score -= 25

    if not data.get("general_liability_limit"):
        issues.append({"field": "general_liability_limit", "message": "General Liability missing."})
        score -= 25

    if not data.get("workers_compensation"):
        warnings.append({"field": "workers_compensation", "message": "Workers Compensation not confirmed."})
        score -= 10

    if issues:
        status = "Blocked"
    elif warnings:
        status = "Pending"
    else:
        status = "Ready"

    return DocumentIntelligenceResult(
        document_type="Certificate of Insurance",
        status=status,
        score=max(score, 0),
        extracted_data=data,
        issues=issues,
        warnings=warnings,
        confidence=data.get("confidence", 0.0),
        summary="COI validation completed.",
    )