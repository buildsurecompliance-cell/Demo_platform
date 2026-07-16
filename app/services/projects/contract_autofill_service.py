import logging
import re

from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.extensions import db
from app.models import Project
from app.services.document_intelligence.document_router import (
    normalize_document_type,
)


logger = logging.getLogger(__name__)

GENERAL_CONFIDENCE_THRESHOLD = 0.8
FIELD_CONFIDENCE_THRESHOLD = 0.8
MAX_PROJECT_NAME_LENGTH = 255
PROJECT_NAME_PLACEHOLDERS = {
    "project",
    "new project",
    "placeholder",
    "tbd",
    "to be determined",
    "untitled",
    "untitled project",
}

CONTRACT_FIELDS = (
    "project_name",
    "contract_value",
    "start_date",
    "end_date",
    "required_coverage",
)


def normalize_contract_extraction(data):
    source = data or {}
    field_confidence = source.get("field_confidence") or {}

    raw_start_date = source.get("start_date")
    raw_end_date = source.get("end_date")

    normalized = {
        "document_type": "contract",
        "project_name": normalize_project_name(
            source.get("project_name")
            or source.get("project")
            or source.get("name")
        ),
        "contract_value": normalize_contract_value(
            source.get("contract_value")
        ),
        "start_date": normalize_contract_date(raw_start_date),
        "end_date": normalize_contract_date(raw_end_date),
        "required_coverage": normalize_required_coverage(
            source.get("required_coverage")
            or source.get("minimum_general_liability")
            or source.get("general_liability_requirement")
        ),
        "confidence": normalize_confidence(source.get("confidence")),
        "field_confidence": {
            field_name: normalize_confidence(field_confidence.get(field_name))
            for field_name in CONTRACT_FIELDS
        },
        "notes": _normalize_notes(source.get("notes")),
    }

    if (
        _raw_date_invalid(raw_start_date, normalized["start_date"])
        or _raw_date_invalid(raw_end_date, normalized["end_date"])
        or _date_order_invalid(
            normalized["start_date"],
            normalized["end_date"],
        )
    ):
        normalized["notes"].append("Contract dates are invalid.")
        normalized["start_date"] = None
        normalized["end_date"] = None

    return normalized


def apply_contract_extraction_to_project(doc):
    if not doc or not doc.project_id:
        return False

    if normalize_document_type(doc.document_type) != "contract":
        return False

    project = db.session.get(Project, doc.project_id)

    if not project:
        logger.info(
            "Contract auto-fill skipped document_id=%s reason=project_missing",
            doc.id,
        )
        return False

    extracted_data = normalize_contract_extraction(
        doc.ai_extracted_data or {}
    )
    compliance = doc.ai_compliance_result or {}
    auto_fill = {
        "eligible": _contract_is_eligible(extracted_data, compliance),
        "fields": {},
    }

    if not auto_fill["eligible"]:
        for field_name in CONTRACT_FIELDS:
            auto_fill["fields"][field_name] = {
                "status": "invalid",
            }

        _set_project_auto_fill(doc, auto_fill)
        logger.info(
            "Contract auto-fill skipped document_id=%s project_id=%s reason=not_eligible",
            doc.id,
            project.id,
        )
        return False

    applied_any = False

    field_map = {
        "project_name": (
            "name",
            lambda value: bool(str(value or "").strip()),
            _project_name_is_empty,
        ),
        "contract_value": (
            "contract_value",
            lambda value: value is not None,
            lambda current: current in (None, 0, 0.0),
        ),
        "start_date": (
            "start_date",
            lambda value: value is not None,
            lambda current: current is None,
        ),
        "end_date": (
            "end_date",
            lambda value: value is not None,
            lambda current: current is None,
        ),
        "required_coverage": (
            "required_coverage",
            lambda value: value is not None,
            lambda current: current in (None, 0),
        ),
    }

    for field_name, (project_attr, has_value, is_empty) in field_map.items():
        value = extracted_data.get(field_name)
        confidence = (
            extracted_data.get("field_confidence") or {}
        ).get(field_name, 0.0)

        status = _auto_fill_status(
            value=value,
            confidence=confidence,
            current_value=getattr(project, project_attr),
            has_value=has_value,
            is_empty=is_empty,
        )

        if status == "applied":
            setattr(project, project_attr, _project_value(field_name, value))
            applied_any = True

        elif status == "preserved":
            logger.info(
                "Contract auto-fill preserved existing value document_id=%s project_id=%s field=%s",
                doc.id,
                project.id,
                field_name,
            )

        auto_fill["fields"][field_name] = {
            "status": status,
            "confidence": confidence,
        }

    _set_project_auto_fill(doc, auto_fill)

    if applied_any:
        logger.info(
            "Contract auto-fill applied document_id=%s project_id=%s",
            doc.id,
            project.id,
        )

    return applied_any


def normalize_project_name(value):
    if value is None:
        return None

    normalized = str(value).strip()

    if not normalized:
        return None

    return normalized[:MAX_PROJECT_NAME_LENGTH]


def _project_name_is_empty(value):
    normalized = str(value or "").strip().lower()

    return not normalized or normalized in PROJECT_NAME_PLACEHOLDERS


def normalize_money(value):
    if value is None or value == "":
        return None

    if isinstance(value, (int, float, Decimal)):
        amount = Decimal(str(value))
    else:
        text = str(value).strip().lower()
        multiplier = Decimal("1")

        if "million" in text:
            multiplier = Decimal("1000000")
            text = text.replace("million", "")
        elif re.search(r"\d\s*m\b", text):
            multiplier = Decimal("1000000")
            text = re.sub(r"\s*m\b", "", text)

        text = (
            text
            .replace("$", "")
            .replace(",", "")
            .replace("usd", "")
            .strip()
        )

        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            return None

        try:
            amount = Decimal(match.group(0)) * multiplier
        except InvalidOperation:
            return None

    if amount <= 0:
        return None

    return int(amount)


def normalize_contract_value(value):
    if _contains_any(
        value,
        (
            "insurance",
            "liability",
            "retainage",
            "retention",
            "allowance",
            "liquidated damages",
            "bond",
            "alternate",
        ),
    ):
        return None

    return normalize_money(value)


def normalize_contract_date(value):
    if value is None or value == "":
        return None

    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value.isoformat()

    text = str(value).strip()

    for date_format in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, date_format).date().isoformat()
        except ValueError:
            continue

    return None


def normalize_required_coverage(value):
    if _contains_any(
        value,
        (
            "umbrella",
            "automobile",
            "auto liability",
            "workers compensation",
            "workers' compensation",
            "aggregate",
        ),
    ):
        return None

    amount = normalize_money(value)

    if amount is None or amount < 0:
        return None

    if amount == 0:
        return None

    return amount


def normalize_confidence(value):
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0

    if confidence < 0:
        return 0.0

    if confidence > 1:
        return 1.0

    return confidence


def _contract_is_eligible(extracted_data, compliance):
    if extracted_data.get("confidence", 0.0) < GENERAL_CONFIDENCE_THRESHOLD:
        return False

    if compliance.get("status") != "Ready":
        return False

    if compliance.get("issues"):
        return False

    return True


def _auto_fill_status(
    *,
    value,
    confidence,
    current_value,
    has_value,
    is_empty,
):
    if not has_value(value):
        return "invalid" if confidence >= FIELD_CONFIDENCE_THRESHOLD else "not_found"

    if confidence < FIELD_CONFIDENCE_THRESHOLD:
        return "low_confidence"

    if not is_empty(current_value):
        return "preserved"

    return "applied"


def _project_value(field_name, value):
    if field_name in {"start_date", "end_date"}:
        return datetime.strptime(value, "%Y-%m-%d").date()

    return value


def _set_project_auto_fill(doc, auto_fill):
    compliance = dict(doc.ai_compliance_result or {})
    compliance["project_auto_fill"] = auto_fill
    doc.ai_compliance_result = compliance


def _date_order_invalid(start_date, end_date):
    if not start_date or not end_date:
        return False

    return (
        datetime.strptime(end_date, "%Y-%m-%d").date()
        < datetime.strptime(start_date, "%Y-%m-%d").date()
    )


def _raw_date_invalid(raw_value, normalized_value):
    if raw_value in (None, ""):
        return False

    return normalized_value is None


def _normalize_notes(value):
    if isinstance(value, list):
        return [str(item) for item in value if item]

    if value:
        return [str(value)]

    return []


def _contains_any(value, keywords):
    if value is None:
        return False

    text = str(value).lower()

    return any(keyword in text for keyword in keywords)
