from app.services.document_intelligence import DocumentIntelligenceResult
from app.services.compliance_evidence_service import (
    extract_coi_coverage,
    extract_coi_expiration_date,
)


def validate_coi_data(data):

    issues = []
    warnings = []
    score = 100

    if not extract_coi_expiration_date(data):
        issues.append({"field": "expiration_date", "message": "Expiration date missing."})
        score -= 25

    if extract_coi_coverage(data) is None:
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
