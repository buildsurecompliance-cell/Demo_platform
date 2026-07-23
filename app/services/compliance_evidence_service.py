from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
import logging
import re

from app.services.document_intelligence.document_router import (
    normalize_document_type,
)


MIN_AI_CONFIDENCE = 0.8

EVIDENCE_SOURCE_AI = "document_intelligence"
EVIDENCE_TYPE_COI = "coi"


@dataclass(frozen=True)
class ComplianceEvidence:
    evidence_type: str
    source: str
    value: dict[str, Any]
    confidence: float | None = None
    validated: bool = False
    document_id: int | None = None
    rejection_code: str | None = None
    rejection_message: str | None = None


def collect_coi_evidence(subcontractor):
    documents = list(
        getattr(
            subcontractor,
            "documents",
            [],
        )
        or []
    )

    coi_documents = [
        document
        for document in documents
        if _belongs_to_subcontractor(document, subcontractor)
        if normalize_document_type(
            getattr(
                document,
                "document_type",
                None,
            )
        ) == "coi"
    ]

    if not coi_documents:
        return []

    evidence = [
        _coi_evidence_from_document(document)
        for document in coi_documents
    ]

    return sorted(
        evidence,
        key=lambda item: (
            item.validated,
            _evidence_not_expired(item),
            item.value.get("expiration_date") or date.min,
            item.value.get("analyzed_at") or datetime.min,
            item.value.get("version", 0),
            item.value.get("uploaded_at") or datetime.min,
            item.document_id or 0,
        ),
        reverse=True,
    )


def _belongs_to_subcontractor(document, subcontractor):
    if getattr(
        document,
        "project_id",
        None,
    ) and not getattr(
        document,
        "sub_id",
        None,
    ):
        return False

    subcontractor_id = getattr(
        subcontractor,
        "id",
        None,
    )
    document_sub_id = getattr(
        document,
        "sub_id",
        None,
    )

    if subcontractor_id and document_sub_id:
        return subcontractor_id == document_sub_id

    return True


def _coi_evidence_from_document(document):
    ai_status = getattr(
        document,
        "ai_status",
        None,
    )

    if ai_status == "failed":
        return _rejected_evidence(
            document,
            "COI_DOCUMENT_UNREADABLE",
            "COI document could not be read by Document Intelligence.",
        )

    if ai_status != "analyzed":
        return _rejected_evidence(
            document,
            "COI_DOCUMENT_PARTIAL",
            "COI document has not completed Document Intelligence processing.",
        )

    compliance = _safe_dict(
        getattr(
            document,
            "ai_compliance_result",
            None,
        )
    )

    if not compliance:
        return _rejected_evidence(
            document,
            "AI_VALIDATION_FAILED",
            "COI validator result is missing.",
        )

    extracted_data = _safe_dict(
        getattr(
            document,
            "ai_extracted_data",
            None,
        )
    )

    confidence = _usable_number(
        getattr(
            document,
            "ai_confidence",
            None,
        )
    )

    if confidence is None:
        confidence = _usable_number(
            compliance.get("confidence")
        )

    if confidence is None:
        confidence = _usable_number(
            extracted_data.get("confidence")
        )

    if confidence is None or confidence < MIN_AI_CONFIDENCE:
        return _rejected_evidence(
            document,
            "AI_CONFIDENCE_LOW",
            "COI evidence confidence is below the required threshold.",
            confidence=confidence,
        )

    issues = compliance.get(
        "issues",
        [],
    )

    if issues or compliance.get("status") == "Blocked":
        return _rejected_evidence(
            document,
            "AI_VALIDATION_FAILED",
            "COI validator reported blocking issues.",
            confidence=confidence,
        )

    expiration_date = extract_coi_expiration_date(
        extracted_data
    )

    coverage = extract_coi_coverage(
        extracted_data
    )

    logging_payload = {
        "document_id": getattr(document, "id", None),
        "extracted_keys": sorted(extracted_data.keys()),
        "normalized_expiration": expiration_date,
        "normalized_coverage": coverage,
        "confidence": confidence,
    }

    if not expiration_date or coverage is None:
        logging.getLogger(__name__).info(
            "COI evidence rejected after normalization %s",
            logging_payload,
        )
        return _rejected_evidence(
            document,
            "AI_VALIDATION_FAILED",
            "COI evidence is missing validated expiration or coverage data.",
            confidence=confidence,
        )

    evidence = ComplianceEvidence(
        evidence_type=EVIDENCE_TYPE_COI,
        source=EVIDENCE_SOURCE_AI,
        value={
            "expiration_date": expiration_date,
            "coverage": coverage,
            "carrier": extracted_data.get("insurance_carrier"),
            "policy_number": extracted_data.get("policy_number"),
            "version": _version(document),
            "uploaded_at": _uploaded_at(document),
            "analyzed_at": _analyzed_at(document),
        },
        confidence=confidence,
        validated=True,
        document_id=getattr(
            document,
            "id",
            None,
        ),
    )
    logging.getLogger(__name__).info(
        "COI evidence generated %s evidence=%s",
        logging_payload,
        evidence,
    )

    return evidence


def _rejected_evidence(
    document,
    code,
    message,
    confidence=None,
):
    return ComplianceEvidence(
        evidence_type=EVIDENCE_TYPE_COI,
        source=EVIDENCE_SOURCE_AI,
        value={
            "version": _version(document),
            "uploaded_at": _uploaded_at(document),
            "analyzed_at": _analyzed_at(document),
        },
        confidence=confidence,
        validated=False,
        document_id=getattr(
            document,
            "id",
            None,
        ),
        rejection_code=code,
        rejection_message=message,
    )


def coi_evidence_from_document(document):
    return _coi_evidence_from_document(document)


def extract_coi_expiration_date(extracted_data):
    extracted_data = _safe_dict(
        extracted_data
    )

    for key in (
        "expiration_date",
        "policy_expiration_date",
        "policy_expiration",
        "policy_exp",
        "policy_exp_date",
        "policy_expiry_date",
        "coi_expiration",
        "expiration",
        "expiry_date",
    ):
        expiration = normalize_coi_expiration_date(
            extracted_data.get(key)
        )

        if expiration:
            return expiration

    coverage_expirations = []

    for coverage_key in (
        "general_liability",
        "automobile_liability",
        "umbrella_liability",
        "workers_compensation",
    ):
        coverage = _safe_dict(extracted_data.get(coverage_key))
        expiration = normalize_coi_expiration_date(
            coverage.get("expiration_date")
        )

        if expiration:
            coverage_expirations.append(expiration)

    if coverage_expirations:
        future_dates = [
            expiration
            for expiration in coverage_expirations
            if expiration >= date.today()
        ]

        return min(future_dates or coverage_expirations)

    return None


def extract_coi_coverage(extracted_data):
    extracted_data = _safe_dict(
        extracted_data
    )

    for key in (
        "general_liability_limit",
        "general_liability_each_occurrence",
        "general_liability_each_occurrence_limit",
        "gl_each_occurrence",
        "each_occurrence",
        "coverage",
    ):
        coverage = normalize_coverage_amount(
            extracted_data.get(key)
        )

        if coverage is not None:
            return coverage

    general_liability = _safe_dict(
        extracted_data.get("general_liability")
    )
    coverage = normalize_coverage_amount(
        general_liability.get("each_occurrence")
    )

    if coverage is not None:
        return coverage

    return None


def normalize_coi_expiration_date(value):
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if not value:
        return None

    normalized = str(value).strip()

    if not normalized:
        return None

    try:
        return datetime.fromisoformat(
            normalized.replace("Z", "+00:00")
        ).date()
    except ValueError:
        pass

    for date_format in (
        "%m/%d/%Y",
        "%m/%d/%y",
        "%m-%d-%Y",
        "%m-%d-%y",
        "%Y-%m-%d",
        "%B %d, %Y",
        "%b %d, %Y",
    ):
        try:
            return datetime.strptime(
                normalized,
                date_format,
            ).date()
        except ValueError:
            continue

    return None


def _safe_dict(value):
    if isinstance(value, dict):
        return value

    return {}


def _version(document):
    value = getattr(
        document,
        "version",
        0,
    )

    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _uploaded_at(document):
    value = getattr(
        document,
        "uploaded_at",
        None,
    )

    if isinstance(value, datetime):
        return value

    return None


def _analyzed_at(document):
    value = getattr(
        document,
        "ai_analyzed_at",
        None,
    )

    if isinstance(value, datetime):
        return value

    return None


def _evidence_not_expired(evidence):
    expiration = evidence.value.get("expiration_date")

    return bool(
        evidence.validated
        and isinstance(expiration, date)
        and expiration >= date.today()
    )


def _usable_number(value):
    return normalize_coverage_amount(value)


def normalize_coverage_amount(value):
    if value is None:
        return None

    if isinstance(value, (int, float)):
        number = float(value)

        if number <= 0:
            return None

        return number

    text = str(value).strip()

    if not text:
        return None

    million_match = re.search(
        r"(?<![A-Za-z0-9])(\d+(?:[.,]\d+)?)\s*(?:m|million)\b",
        text,
        flags=re.IGNORECASE,
    )

    if million_match:
        try:
            number = float(
                million_match.group(1).replace(",", ".")
            ) * 1_000_000
        except ValueError:
            return None

        return number if number > 0 else None

    amount_text = re.sub(
        r"\bUSD\b",
        "",
        text,
        flags=re.IGNORECASE,
    ).replace("$", "")

    amount_match = re.search(
        r"\d[\d,.\s]*",
        amount_text,
        flags=re.IGNORECASE,
    )

    if not amount_match:
        return None

    normalized = amount_match.group(0).strip().replace(" ", "")

    if not normalized:
        return None

    if "," in normalized and "." in normalized:
        normalized = normalized.replace(",", "")
    elif normalized.count(".") > 1:
        normalized = normalized.replace(".", "")
    elif normalized.count(",") > 1:
        normalized = normalized.replace(",", "")
    elif "." in normalized:
        whole, fraction = normalized.rsplit(".", 1)
        if len(fraction) == 3 and whole.isdigit():
            normalized = whole + fraction
    elif "," in normalized:
        whole, fraction = normalized.rsplit(",", 1)
        if len(fraction) == 3 and whole.isdigit():
            normalized = whole + fraction
        else:
            normalized = whole + "." + fraction

    try:
        number = float(
            normalized
        )
    except (TypeError, ValueError):
        return None

    if number <= 0:
        return None

    return number
