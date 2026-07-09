from app.services.document_intelligence import DocumentIntelligenceResult


def validate_drug_test_data(data):

    issues = []
    score = 100

    if not data.get("person_name"):
        issues.append({"field": "person_name", "message": "Person name missing."})
        score -= 30

    if data.get("result") != "negative":
        issues.append({"field": "result", "message": "Drug test is not confirmed negative."})
        score -= 50

    status = "Blocked" if issues else "Ready"

    return DocumentIntelligenceResult(
        document_type="Drug Test",
        status=status,
        score=max(score, 0),
        extracted_data=data,
        issues=issues,
        warnings=[],
        confidence=data.get("confidence", 0.0),
        summary="Drug test validation completed.",
    )