from app.services.document_intelligence import DocumentIntelligenceResult


def validate_w9_data(data):

    issues = []
    score = 100

    if not data.get("legal_name"):
        issues.append({"field": "legal_name", "message": "Legal name missing."})
        score -= 30

    if not data.get("tax_id_last4"):
        issues.append({"field": "tax_id_last4", "message": "Tax ID not confirmed."})
        score -= 40

    status = "Blocked" if issues else "Ready"

    return DocumentIntelligenceResult(
        document_type="W-9",
        status=status,
        score=max(score, 0),
        extracted_data=data,
        issues=issues,
        warnings=[],
        confidence=data.get("confidence", 0.0),
        summary="W-9 validation completed.",
    )