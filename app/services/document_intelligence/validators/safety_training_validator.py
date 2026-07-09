from app.services.document_intelligence import DocumentIntelligenceResult


def validate_safety_training_data(data):

    issues = []
    score = 100

    if not data.get("person_name"):
        issues.append({"field": "person_name", "message": "Person name missing."})
        score -= 30

    if not data.get("training_name"):
        issues.append({"field": "training_name", "message": "Training name missing."})
        score -= 30

    status = "Blocked" if issues else "Ready"

    return DocumentIntelligenceResult(
        document_type="Safety Training",
        status=status,
        score=max(score, 0),
        extracted_data=data,
        issues=issues,
        warnings=[],
        confidence=data.get("confidence", 0.0),
        summary="Safety training validation completed.",
    )