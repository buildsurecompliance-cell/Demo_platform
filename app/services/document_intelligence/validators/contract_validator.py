from app.services.document_intelligence import DocumentIntelligenceResult


MIN_CONTRACT_CONFIDENCE = 0.8


def validate_contract_data(data):
    issues = []
    warnings = []
    score = 100

    confidence = data.get("confidence") or 0.0

    if confidence < MIN_CONTRACT_CONFIDENCE:
        issues.append(
            {
                "field": "confidence",
                "message": "Contract extraction confidence is too low.",
            }
        )
        score -= 40

    if data.get("document_type") != "contract":
        issues.append(
            {
                "field": "document_type",
                "message": "Contract document type was not confirmed.",
            }
        )
        score -= 25

    field_confidence = data.get("field_confidence") or {}

    for field_name in (
        "project_name",
        "contract_value",
        "start_date",
        "end_date",
        "required_coverage",
    ):
        if field_name not in field_confidence:
            warnings.append(
                {
                    "field": field_name,
                    "message": "Field confidence missing.",
                }
            )
            score -= 5

    if issues:
        status = "Blocked"
    elif warnings:
        status = "Pending"
    else:
        status = "Ready"

    return DocumentIntelligenceResult(
        document_type="Contract",
        status=status,
        score=max(score, 0),
        extracted_data=data,
        issues=issues,
        warnings=warnings,
        confidence=confidence,
        summary="Contract validation completed.",
    )
