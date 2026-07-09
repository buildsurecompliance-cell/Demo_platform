from app.services.document_intelligence import DocumentIntelligenceResult


def validate_osha_data(data):

    issues = []
    score = 100

    if not data.get("holder_name"):
        issues.append({"field": "holder_name", "message": "Card holder name missing."})
        score -= 30

    if not data.get("training_type"):
        issues.append({"field": "training_type", "message": "OSHA training type missing."})
        score -= 30

    status = "Blocked" if issues else "Ready"

    return DocumentIntelligenceResult(
        document_type="OSHA",
        status=status,
        score=max(score, 0),
        extracted_data=data,
        issues=issues,
        warnings=[],
        confidence=data.get("confidence", 0.0),
        summary="OSHA validation completed.",
    )