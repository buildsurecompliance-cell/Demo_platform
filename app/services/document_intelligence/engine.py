import hashlib
import base64
import logging
import os
import re
import zlib

from datetime import date, datetime

from app.services.document_intelligence.document_router import (
    normalize_document_type,
)

from app.services.document_intelligence.openai_document_parser import (
    parse_document_with_ai,
)

from app.services.document_intelligence.validators import (
    validate_background_check_data,
    validate_coi_data,
    validate_contract_data,
    validate_drug_test_data,
    validate_license_data,
    validate_osha_data,
    validate_safety_training_data,
    validate_w9_data,
)
from app.services.compliance_evidence_service import (
    normalize_coi_expiration_date,
    normalize_coverage_amount,
)
from app.services.projects.contract_autofill_service import (
    normalize_contract_extraction,
)


logger = logging.getLogger(__name__)


def get_mock_data_for_document(category):

    mock_data = {
        "coi": {
            "confidence": 0.0,
            "notes": "COI mock data must be parsed from a supplied document.",
        },
        "w9": {
            "legal_name": "ABC Flooring LLC",
            "tax_id_last4": "1234",
            "confidence": 0.91,
        },
        "license": {
            "business_name": "ABC Flooring LLC",
            "license_number": "LIC-987654",
            "expiration_date": "2027-12-31",
            "confidence": 0.9,
        },
        "osha": {
            "holder_name": "John Smith",
            "training_type": "OSHA 10",
            "completion_date": "2026-02-15",
            "confidence": 0.88,
        },
        "drug_test": {
            "person_name": "John Smith",
            "result": "negative",
            "test_date": "2026-03-01",
            "confidence": 0.89,
        },
        "safety_training": {
            "person_name": "John Smith",
            "training_name": "Site Safety Orientation",
            "completion_date": "2026-03-10",
            "confidence": 0.9,
        },
        "background_check": {
            "person_name": "John Smith",
            "status": "clear",
            "completed_date": "2026-03-12",
            "confidence": 0.87,
        },
    }

    return mock_data.get(
        category,
        {
            "confidence": 0.0,
            "notes": "Unsupported document type.",
        }
    )


def _failure_result(error, category):
    return {
        "success": False,
        "error": error,
        "extracted_data": {},
        "compliance": {},
        "category": category,
    }


def _read_document_bytes(file_path):
    with open(file_path, "rb") as file:
        return file.read()


def _document_hash(file_bytes):
    return hashlib.sha256(file_bytes).hexdigest()


def _decode_document_text(file_bytes):
    pdf_text = _extract_pdf_text(file_bytes)

    if _has_usable_text(pdf_text):
        return pdf_text

    for encoding in ("utf-8", "latin-1"):
        text = file_bytes.decode(
            encoding,
            errors="ignore",
        )

        if _has_usable_text(text):
            return text

    return ""


def _extract_pdf_text(file_bytes):
    if not file_bytes.startswith(b"%PDF"):
        return ""

    strings = []

    for object_match in re.finditer(
        rb"\d+\s+\d+\s+obj(.*?)endobj",
        file_bytes,
        flags=re.DOTALL,
    ):
        pdf_object = object_match.group(1)

        if b"stream" not in pdf_object:
            continue

        try:
            stream = (
                pdf_object
                .split(b"stream", 1)[1]
                .rsplit(b"endstream", 1)[0]
                .strip()
            )
            decoded = _decode_pdf_stream(
                pdf_object,
                stream,
            )
        except Exception:
            continue

        if not decoded:
            continue

        strings.extend(
            _extract_pdf_text_strings(
                decoded.decode(
                    "latin-1",
                    errors="ignore",
                )
            )
        )

    return "\n".join(
        value for value in strings if value.strip()
    )


def _decode_pdf_stream(pdf_object, stream):
    decoded = stream

    if b"ASCII85Decode" in pdf_object:
        decoded = base64.a85decode(
            decoded,
            adobe=True,
        )

    if b"FlateDecode" in pdf_object:
        decoded = zlib.decompress(
            decoded
        )

    return decoded


def _extract_pdf_text_strings(text_stream):
    strings = []

    for literal in re.findall(
        r"\((?:\\.|[^\\)])*\)\s*Tj",
        text_stream,
    ):
        strings.append(
            _decode_pdf_literal(
                literal.rsplit(")", 1)[0][1:]
            )
        )

    for array_body in re.findall(
        r"\[(.*?)\]\s*TJ",
        text_stream,
        flags=re.DOTALL,
    ):
        for literal in re.findall(
            r"\((?:\\.|[^\\)])*\)",
            array_body,
        ):
            strings.append(
                _decode_pdf_literal(
                    literal[1:-1]
                )
            )

    return strings


def _decode_pdf_literal(value):
    return (
        value
        .replace(r"\(", "(")
        .replace(r"\)", ")")
        .replace(r"\\", "\\")
    )


def _has_usable_text(text):
    return bool(
        text
        and re.search(
            r"[A-Za-z0-9]{3,}",
            text,
        )
    )


def _first_match(text, patterns):
    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        )

        if match:
            return match.group(1).strip(" \t:-")

    return None


def _lines(text):
    cleaned = []

    for line in text.splitlines():
        value = line.strip()

        if value:
            cleaned.append(value)

    return cleaned


def _is_label(line, labels):
    normalized = re.sub(
        r"[^a-z0-9]+",
        " ",
        line.lower(),
    ).strip()

    return normalized in labels


def _next_value_after_label(text, labels):
    lines = _lines(text)

    for index, line in enumerate(lines[:-1]):
        if _is_label(line, labels):
            candidate = lines[index + 1].strip()

            if candidate:
                return candidate

    return None


def _normalize_mock_date(value):
    if not value:
        return None

    text = str(value).strip()

    for date_format in (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%B %d, %Y",
        "%b %d, %Y",
    ):
        try:
            return datetime.strptime(
                text,
                date_format,
            ).date().isoformat()
        except ValueError:
            continue

    return text


def _extract_contract_data_from_text(text):
    project_name = _first_match(
        text,
        (
            r"project\s+name\s*[:\-]\s*(.+)",
            r"project\s*:\s*(.+)",
            r"project\s+known\s+as\s+(.+?)\s+is\s+located",
            r"contract\s+for\s*[:\-]\s*(.+)",
            r"agreement\s+for\s*[:\-]\s*(.+)",
        ),
    ) or _next_value_after_label(
        text,
        {
            "project",
            "project name",
        },
    )
    contract_value = _first_match(
        text,
        (
            r"contract\s+(?:sum|value|price|amount)\s*[:\-]\s*([^\n\r]+)",
            r"total\s+contract\s+(?:sum|value|price|amount)\s*[:\-]\s*([^\n\r]+)",
            r"contract\s+sum\s+is\s+([^\n\r.]+)",
            r"contract\s+amount\s+is\s+([^\n\r.]+)",
        ),
    ) or _next_value_after_label(
        text,
        {
            "contract amount",
            "contract sum",
            "contract value",
            "contract price",
            "total contract amount",
            "total contract value",
        },
    )
    start_date = _normalize_mock_date(
        _first_match(
            text,
            (
                r"(?:start|commencement)\s+date\s*[:\-]\s*([0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}|[A-Za-z]+\s+\d{1,2},\s+\d{4})",
                r"project\s+start\s*[:\-]\s*([0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}|[A-Za-z]+\s+\d{1,2},\s+\d{4})",
                r"contractual\s+start\s+date\s+is\s+([A-Za-z]+\s+\d{1,2},\s+\d{4}|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{4})",
            ),
        ) or _next_value_after_label(
            text,
            {
                "start date",
                "commencement date",
                "project start",
                "project start date",
            },
        )
    )
    end_date = _normalize_mock_date(
        _first_match(
            text,
            (
                r"(?:end|completion|substantial\s+completion)\s+date\s*[:\-]\s*([0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}|[A-Za-z]+\s+\d{1,2},\s+\d{4})",
                r"substantial\s+completion\s*[:\-]\s*([0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}|[A-Za-z]+\s+\d{1,2},\s+\d{4})",
                r"project\s+end\s*[:\-]\s*([0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}|[A-Za-z]+\s+\d{1,2},\s+\d{4})",
                r"substantial\s+completion\s+is\s+required\s+no\s+later\s+than\s+([A-Za-z]+\s+\d{1,2},\s+\d{4}|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{4})",
            ),
        ) or _next_value_after_label(
            text,
            {
                "end date",
                "completion date",
                "substantial completion",
                "substantial completion date",
                "project end",
                "project end date",
            },
        )
    )
    required_coverage = _first_match(
        text,
        (
            r"minimum\s+general\s+liability\s*[:\-]\s*([^\n\r]+)",
            r"required\s+general\s+liability\s*[:\-]\s*([^\n\r]+)",
            r"commercial\s+general\s+liability\s*[:\-]\s*(\$?[0-9][0-9,]*(?:\.\d+)?(?:\s*(?:million|m))?)",
            r"commercial\s+general\s+liability\s+(\$?[0-9][0-9,]*(?:\.\d+)?(?:\s*(?:million|m))?)",
            r"general\s+liability\s+(?:per\s+occurrence\s+)?(?:limit|required|requirement)\s*[:\-]\s*([^\n\r]+)",
            r"required\s+coverage\s*[:\-]\s*([^\n\r]+)",
        ),
    ) or _next_value_after_label(
        text,
        {
            "commercial general liability",
            "general liability",
            "minimum general liability",
            "required general liability",
            "general liability limit",
            "general liability requirement",
            "required coverage",
        },
    )

    extracted = {
        "document_type": "contract",
        "project_name": project_name,
        "contract_value": contract_value,
        "start_date": start_date,
        "end_date": end_date,
        "required_coverage": required_coverage,
        "confidence": 0.95,
        "field_confidence": {
            "project_name": 0.92 if project_name else 0.0,
            "contract_value": 0.92 if contract_value else 0.0,
            "start_date": 0.92 if start_date else 0.0,
            "end_date": 0.92 if end_date else 0.0,
            "required_coverage": 0.92 if required_coverage else 0.0,
        },
        "notes": [],
    }

    if not any(
        extracted.get(field)
        for field in (
            "project_name",
            "contract_value",
            "start_date",
            "end_date",
            "required_coverage",
        )
    ):
        return None

    return extracted


def _parse_contract_in_mock_mode(file_path):
    try:
        file_bytes = _read_document_bytes(file_path)
    except OSError as error:
        return {
            "success": False,
            "error": f"Document file could not be read: {error.__class__.__name__}",
            "data": None,
            "document_hash": None,
        }

    document_hash = _document_hash(file_bytes)
    text = _decode_document_text(file_bytes)

    if not _has_usable_text(text):
        return {
            "success": False,
            "error": "Document text could not be extracted.",
            "data": None,
            "document_hash": document_hash,
        }

    extracted_data = _extract_contract_data_from_text(text)

    if not extracted_data:
        return {
            "success": False,
            "error": "Contract fields could not be extracted from the document.",
            "data": None,
            "document_hash": document_hash,
        }

    return {
        "success": True,
        "error": None,
        "data": extracted_data,
        "document_hash": document_hash,
    }


def _date_pattern():
    return (
        r"([0-9]{4}-[0-9]{2}-[0-9]{2}|"
        r"[0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}|"
        r"[0-9]{1,2}-[0-9]{1,2}-[0-9]{2,4}|"
        r"[A-Za-z]+\s+\d{1,2},\s+\d{4}|"
        r"[A-Za-z]{3}\s+\d{1,2},\s+\d{4})"
    )


def _normalize_coi_mock_date(value):
    normalized = normalize_coi_expiration_date(value)

    if normalized:
        return normalized.isoformat()

    return None


def _extract_labeled_date(text, labels):
    date_value = _date_pattern()

    for label in labels:
        match = re.search(
            rf"\b{label}\b\s*[:#\-]?\s*{date_value}",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        )

        if match:
            return _normalize_coi_mock_date(match.group(1))

    return None


def _amount_regex():
    return (
        r"(?:USD\s*)?\$?\s*\d+(?:[\s,.\d]*\d)?"
        r"(?:\s*(?:m|million))?"
    )


def _extract_labeled_value(text, labels):
    for label in labels:
        match = re.search(
            rf"\b{label}\b\s*[:#\-]?\s*([^\n\r]+)",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        )

        if match:
            value = match.group(1).strip()

            if value:
                return value

    return None


def _compact_label(value):
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        value.lower(),
    ).strip()


def _line_date_candidates(text):
    date_value = _date_pattern()
    lines = _lines(text)
    candidates = []

    for index, line in enumerate(lines):
        for match in re.finditer(
            date_value,
            line,
            flags=re.IGNORECASE,
        ):
            normalized = _normalize_coi_mock_date(match.group(1))

            if normalized:
                candidates.append(
                    {
                        "line_index": index,
                        "line": line,
                        "value": normalized,
                    }
                )

    return candidates


def _extract_policy_dates(text):
    lines = _lines(text)
    dates = _line_date_candidates(text)
    expirations = []
    effective_dates = []
    expiration_markers = (
        "policy exp",
        "policy expiration",
        "expiration date",
        "policy exp date",
        "exp date",
    )
    effective_markers = (
        "policy eff",
        "policy effective",
        "effective date",
        "eff date",
    )
    expiration_label_patterns = (
        r"policy\s+exp(?:iration)?(?:\s+date)?",
        r"expiration\s+date",
        r"policy\s+exp\s+date",
    )
    effective_label_patterns = (
        r"policy\s+eff(?:ective)?(?:\s+date)?",
        r"effective\s+date",
    )

    for index, line in enumerate(lines):
        normalized_line = _compact_label(line)

        for marker_set, bucket in (
            (expiration_markers, expirations),
            (effective_markers, effective_dates),
        ):
            if not any(marker in normalized_line for marker in marker_set):
                continue

            label_patterns = (
                expiration_label_patterns
                if bucket is expirations
                else effective_label_patterns
            )
            same_line_dates = []

            for label_pattern in label_patterns:
                for match in re.finditer(
                    rf"\b{label_pattern}\b\s*[:#\-]?\s*{_date_pattern()}",
                    line,
                    flags=re.IGNORECASE,
                ):
                    normalized = _normalize_coi_mock_date(match.group(1))

                    if normalized:
                        same_line_dates.append(normalized)

            if same_line_dates:
                for value in same_line_dates:
                    if value not in bucket:
                        bucket.append(value)
                continue

            nearby_dates = [
                item
                for item in dates
                if index <= item["line_index"] <= index + 4
            ]

            if not nearby_dates:
                nearby_dates = [
                    item
                    for item in dates
                    if max(0, index - 2) <= item["line_index"] <= index + 8
                ]

            for item in nearby_dates:
                if item["value"] not in bucket:
                    bucket.append(item["value"])

    if not expirations:
        for label in (
            r"policy\s+exp(?:iration)?(?:\s+date)?",
            r"policy\s+expires",
            r"policy\s+end\s+date",
            r"expiration\s+date",
            r"policy\s+exp\s+date",
        ):
            value = _extract_labeled_date(text, (label,))

            if value and value not in expirations:
                expirations.append(value)

    if not effective_dates:
        for label in (
            r"policy\s+eff(?:ective)?(?:\s+date)?",
            r"effective\s+date",
        ):
            value = _extract_labeled_date(text, (label,))

            if value and value not in effective_dates:
                effective_dates.append(value)

    effective_header_index = None
    expiration_header_index = None

    for index, line in enumerate(lines):
        normalized_line = _compact_label(line)

        if (
            effective_header_index is None
            and "policy effective date" in normalized_line
        ):
            effective_header_index = index

        if (
            expiration_header_index is None
            and "policy expiration date" in normalized_line
        ):
            expiration_header_index = index

    if (
        effective_header_index is not None
        and expiration_header_index is not None
        and expiration_header_index >= effective_header_index
    ):
        table_dates = [
            item["value"]
            for item in dates
            if expiration_header_index < item["line_index"] <= expiration_header_index + 8
        ]

        if len(table_dates) >= 2:
            effective_dates = [table_dates[0]]
            expirations = [table_dates[1]]

    if expirations and effective_dates:
        effective_set = set(effective_dates)
        filtered = [
            value
            for value in expirations
            if value not in effective_set
        ]

        if filtered:
            expirations = filtered

    logger.debug(
        "COI date candidates labels=%s expirations=%s effective_dates=%s",
        {
            "expiration": expiration_markers,
            "effective": effective_markers,
        },
        expirations,
        effective_dates,
    )

    return {
        "effective_dates": effective_dates,
        "expiration_dates": expirations,
    }


def _parse_iso_date(value):
    normalized = normalize_coi_expiration_date(value)

    if normalized:
        return normalized

    return None


def _select_overall_expiration(expiration_dates):
    parsed = [
        _parse_iso_date(value)
        for value in expiration_dates
    ]
    parsed = [
        value
        for value in parsed
        if value
    ]

    if not parsed:
        return None

    future_dates = [
        value
        for value in parsed
        if value >= date.today()
    ]

    selected = min(future_dates or parsed)

    return selected.isoformat()


def _amount_candidates(text, labels, *, search_lines_after=3):
    amount = _amount_regex()
    lines = _lines(text)
    matches = []

    for index, line in enumerate(lines):
        normalized_line = _compact_label(line)

        if not any(
            re.search(label, normalized_line, flags=re.IGNORECASE)
            for label in labels
        ):
            continue

        window = " ".join(lines[index:index + search_lines_after + 1])
        label_text = "|".join(labels)
        labeled_match = re.search(
            rf"(?:{label_text}).{{0,80}}?({amount})",
            window,
            flags=re.IGNORECASE,
        )

        raw_amounts = [
            match.group(0)
            for match in re.finditer(
                amount,
                window,
                flags=re.IGNORECASE,
            )
            if "$" in match.group(0)
            or re.search(
                r"\b(?:m|million)\b",
                match.group(0),
                flags=re.IGNORECASE,
            )
        ]

        if labeled_match:
            raw_amounts.insert(0, labeled_match.group(1))

        for raw in raw_amounts:
            if _looks_like_date(raw):
                continue

            normalized = normalize_coverage_amount(raw)

            if normalized is not None and normalized >= 10_000:
                matches.append(
                    {
                        "label": line,
                        "raw": raw,
                        "value": int(normalized),
                    }
                )

    logger.debug(
        "COI amount candidates labels=%s values=%s",
        labels,
        [
            item["value"]
            for item in matches
        ],
    )

    return matches


def _looks_like_date(value):
    text = str(value or "").strip()

    return bool(
        re.fullmatch(
            r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})",
            text,
        )
    )


def _first_amount(text, labels, *, search_lines_after=3):
    candidates = _amount_candidates(
        text,
        labels,
        search_lines_after=search_lines_after,
    )

    if candidates:
        return candidates[0]["value"]

    return None


def _coi_coverage_payload(text, policy_dates):
    expiration_dates = policy_dates["expiration_dates"]
    effective_dates = policy_dates["effective_dates"]
    selected_expiration = _select_overall_expiration(expiration_dates)
    selected_effective = effective_dates[0] if effective_dates else None

    general_liability = {
        "each_occurrence": _first_amount(
            text,
            (
                r"each occurrence",
                r"each occ",
            ),
        ),
        "general_aggregate": _first_amount(
            text,
            (
                r"general aggregate",
                r"gen l aggregate",
                r"gen aggregate",
            ),
        ),
        "products_completed_operations": _first_amount(
            text,
            (
                r"products comp op agg",
                r"products completed operations",
                r"products comp",
            ),
        ),
        "policy_number": _extract_labeled_value(
            text,
            (
                r"policy\s+number",
                r"policy\s+#",
                r"policy\s+no\.?",
            ),
        ),
        "effective_date": selected_effective,
        "expiration_date": selected_expiration,
    }
    automobile_liability = {
        "combined_single_limit": _first_amount(
            text,
            (
                r"combined single limit",
                r"csl",
            ),
        ),
        "expiration_date": selected_expiration,
    }
    umbrella_liability = {
        "each_occurrence": _first_amount(
            text,
            (
                r"umbrella.*each occurrence",
                r"excess.*each occurrence",
            ),
            search_lines_after=5,
        ),
        "aggregate": _first_amount(
            text,
            (
                r"umbrella.*aggregate",
                r"excess.*aggregate",
                r"aggregate",
            ),
            search_lines_after=5,
        ),
        "expiration_date": selected_expiration,
    }
    workers_compensation = {
        "each_accident": _first_amount(
            text,
            (
                r"e l each accident",
                r"each accident",
            ),
        ),
        "disease_each_employee": _first_amount(
            text,
            (
                r"disease ea employee",
                r"disease each employee",
            ),
        ),
        "disease_policy_limit": _first_amount(
            text,
            (
                r"disease policy limit",
                r"policy limit",
            ),
        ),
        "expiration_date": selected_expiration,
    }

    return {
        "general_liability": general_liability,
        "automobile_liability": automobile_liability,
        "umbrella_liability": umbrella_liability,
        "workers_compensation": workers_compensation,
    }


def _extract_general_liability_each_occurrence(text):
    amount = _first_amount(
        text,
        (
            r"each occurrence",
            r"each occ",
        ),
    )

    if amount:
        return str(amount)

    return None


def _extract_trade(text):
    labels = (
        r"trade\s*/\s*operations",
        r"description\s+of\s+operations",
        r"type\s+of\s+work",
        r"scope\s+of\s+work",
        r"scope\s+of\s+operations",
    )

    trade = _extract_labeled_value(text, labels)

    if not trade:
        return None

    trade = re.sub(
        r"\s+",
        " ",
        trade,
    ).strip(" .;")

    return trade or None


def _is_forbidden_contact_context(value):
    normalized = value.lower()

    return any(
        marker in normalized
        for marker in (
            "producer",
            "broker",
            "agency",
            "agent",
            "carrier",
            "insurer",
            "insurance company",
            "certificate holder",
        )
    )


def _is_subcontractor_contact_context(value):
    normalized = value.lower()

    return any(
        marker in normalized
        for marker in (
            "subcontractor",
            "insured",
            "named insured",
            "contact",
        )
    )


def _normalize_extracted_email(value):
    if not value:
        return None

    match = re.search(
        r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}",
        str(value),
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    email = match.group(0).strip().strip(".,;:").lower()

    if not re.fullmatch(
        r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}",
        email,
    ):
        return None

    return email


def _normalize_extracted_phone(value):
    if not value:
        return None

    text = re.sub(
        r"(?:ext\.?|extension|x)\s*\d+\b",
        "",
        str(value),
        flags=re.IGNORECASE,
    ).strip()
    has_plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)

    if len(digits) == 10:
        return f"+1{digits}"

    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"

    if has_plus and len(digits) >= 11:
        return f"+{digits}"

    return None


def _context_before_position(text, position, line_count=3):
    prefix = text[:position]
    lines = _lines(prefix)

    return " ".join(lines[-line_count:])


def _extract_contact_email(text):
    allowed_labels = (
        r"subcontractor\s+email",
        r"insured\s+email",
        r"contact\s+email",
        r"e-?mail",
    )

    for label in allowed_labels:
        for match in re.finditer(
            rf"\b{label}\b\s*[:#\-]?\s*([^\n\r]+)",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        ):
            context = " ".join(
                (
                    _context_before_position(text, match.start()),
                    match.group(0),
                )
            )

            if _is_forbidden_contact_context(context):
                continue

            email = _normalize_extracted_email(match.group(1))

            if email:
                return email

    lines = _lines(text)

    for index, line in enumerate(lines):
        email = _normalize_extracted_email(line)

        if not email:
            continue

        context = " ".join(lines[max(0, index - 3):index + 1])

        if _is_forbidden_contact_context(context):
            continue

        if _is_subcontractor_contact_context(context):
            return email

    return None


def _extract_contact_phone(text):
    phone_value = (
        r"(\+?1?[\s.\-]*(?:\([0-9]{3}\)|[0-9]{3})"
        r"[\s.\-]*[0-9]{3}[\s.\-]*[0-9]{4})"
    )
    allowed_labels = (
        r"subcontractor\s+phone",
        r"insured\s+phone",
        r"contact\s+phone",
        r"phone\s+number",
        r"telephone",
        r"phone",
        r"tel",
    )

    for label in allowed_labels:
        for match in re.finditer(
            rf"\b{label}\b\s*[:#\-]?\s*{phone_value}",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        ):
            context = " ".join(
                (
                    _context_before_position(text, match.start()),
                    match.group(0),
                )
            )

            if _is_forbidden_contact_context(context):
                continue

            phone = _normalize_extracted_phone(match.group(1))

            if phone:
                return phone

    lines = _lines(text)

    for index, line in enumerate(lines):
        match = re.search(phone_value, line, flags=re.IGNORECASE)

        if not match:
            continue

        context = " ".join(lines[max(0, index - 3):index + 1])

        if _is_forbidden_contact_context(context):
            continue

        if _is_subcontractor_contact_context(context):
            phone = _normalize_extracted_phone(match.group(1))

            if phone:
                return phone

    return None


def _extract_coi_data_from_text(text):
    policy_dates = _extract_policy_dates(text)
    coverage_payload = _coi_coverage_payload(text, policy_dates)
    expiration_date = _select_overall_expiration(
        policy_dates["expiration_dates"]
    )
    effective_date = (
        policy_dates["effective_dates"][0]
        if policy_dates["effective_dates"]
        else None
    )
    general_liability = _extract_general_liability_each_occurrence(text)
    trade = _extract_trade(text)
    email = _extract_contact_email(text)
    phone = _extract_contact_phone(text)
    policy_number = _extract_labeled_value(
        text,
        (
            r"policy\s+number",
            r"policy\s+#",
            r"policy\s+no\.?",
        ),
    )
    insurance_carrier = _extract_labeled_value(
        text,
        (
            r"carrier",
            r"insurer",
            r"insurance\s+carrier",
        ),
    )
    producer = _extract_labeled_value(
        text,
        (
            r"producer",
            r"agency",
        ),
    )
    named_insured = _extract_labeled_value(
        text,
        (
            r"named\s+insured",
            r"insured",
        ),
    )
    workers_compensation = bool(
        re.search(
            r"workers'?[\s-]+comp(?:ensation)?",
            text,
            flags=re.IGNORECASE,
        )
    )

    if not expiration_date and not general_liability:
        logger.info(
            "COI extraction failed labels_found=%s date_candidates=%s amount_candidates=%s reason=missing_expiration_and_gl_each_occurrence",
            {
                "policy_exp": bool(policy_dates["expiration_dates"]),
                "policy_eff": bool(policy_dates["effective_dates"]),
                "each_occurrence": general_liability is not None,
            },
            policy_dates["expiration_dates"],
            {
                "general_liability_each_occurrence": general_liability,
            },
        )
        return None

    confidence = 0.92 if expiration_date and general_liability else 0.6
    general_liability_payload = coverage_payload["general_liability"]
    general_liability_payload["each_occurrence"] = (
        int(general_liability)
        if general_liability
        else general_liability_payload.get("each_occurrence")
    )
    general_liability_payload["policy_number"] = (
        general_liability_payload.get("policy_number")
        or policy_number
    )
    general_liability_payload["effective_date"] = (
        general_liability_payload.get("effective_date")
        or effective_date
    )
    general_liability_payload["expiration_date"] = (
        general_liability_payload.get("expiration_date")
        or expiration_date
    )

    logger.info(
        "COI extraction selected expiration=%s coverage_limit=%s confidence=%s",
        expiration_date,
        general_liability,
        confidence,
    )

    return {
        "document_type": "Certificate of Insurance",
        "named_insured": named_insured,
        "insurance_carrier": insurance_carrier,
        "producer": producer,
        "policy_number": policy_number,
        "effective_date": effective_date,
        "expiration_date": expiration_date,
        "general_liability_limit": general_liability,
        "coverage_limit": general_liability,
        "coverage_details": coverage_payload,
        "general_liability": general_liability_payload,
        "automobile_liability": coverage_payload["automobile_liability"],
        "umbrella_liability": coverage_payload["umbrella_liability"],
        "auto_liability_limit": None,
        "workers_compensation": coverage_payload["workers_compensation"],
        "workers_compensation_confirmed": workers_compensation,
        "umbrella_limit": None,
        "additional_insured": False,
        "waiver_of_subrogation": False,
        "primary_non_contributory": False,
        "trade": trade,
        "email": email,
        "phone": phone,
        "confidence": confidence,
        "missing_fields": [],
        "policy_expiration_dates": policy_dates["expiration_dates"],
        "policy_effective_dates": policy_dates["effective_dates"],
        "notes": "Mock COI data parsed from supplied document text.",
    }


def _parse_coi_in_mock_mode(file_path):
    try:
        file_bytes = _read_document_bytes(file_path)
    except OSError as error:
        return {
            "success": False,
            "error": f"Document file could not be read: {error.__class__.__name__}",
            "data": None,
            "document_hash": None,
        }

    document_hash = _document_hash(file_bytes)
    text = _decode_document_text(file_bytes)

    if not _has_usable_text(text):
        return {
            "success": False,
            "error": "Document text could not be extracted.",
            "data": None,
            "document_hash": document_hash,
        }

    extracted_data = _extract_coi_data_from_text(text)

    if not extracted_data:
        return {
            "success": False,
            "error": "COI fields could not be extracted from the document.",
            "data": None,
            "document_hash": document_hash,
        }

    return {
        "success": True,
        "error": None,
        "data": extracted_data,
        "document_hash": document_hash,
    }


def validate_by_category(category, extracted_data):
    if category == "contract":
        return validate_contract_data(extracted_data)

    if category == "w9":
        return validate_w9_data(extracted_data)

    if category == "license":
        return validate_license_data(extracted_data)

    if category == "osha":
        return validate_osha_data(extracted_data)

    if category == "drug_test":
        return validate_drug_test_data(extracted_data)

    if category == "safety_training":
        return validate_safety_training_data(extracted_data)

    if category == "background_check":
        return validate_background_check_data(extracted_data)

    if category == "coi":
        return validate_coi_data(extracted_data)

    return None


def analyze_document_intelligence(
    file_path,
    document_type,
):

    category = normalize_document_type(
        document_type
    )

    if category == "other":
        return _failure_result(
            f"Unsupported document type: {document_type}",
            category,
        )

    mock_mode = os.getenv(
        "AI_MOCK_MODE",
        "false"
    ).lower() == "true"

    document_hash = None

    if mock_mode and category in {"contract", "coi"}:
        if category == "contract":
            parse_result = _parse_contract_in_mock_mode(
                file_path
            )
        else:
            parse_result = _parse_coi_in_mock_mode(
                file_path
            )

        document_hash = parse_result.get(
            "document_hash"
        )

        if not parse_result["success"]:
            logger.info(
                "Document intelligence mock extraction failed category=%s file_hash=%s reason=%s",
                category,
                document_hash[:12] if document_hash else None,
                parse_result["error"],
            )
            return _failure_result(
                parse_result["error"],
                category,
            )

        extracted_data = parse_result["data"]

    elif mock_mode:
        extracted_data = get_mock_data_for_document(
            category
        )

    else:
        parse_result = parse_document_with_ai(
            file_path=file_path,
            category=category,
        )

        if not parse_result["success"]:
            return _failure_result(
                parse_result["error"],
                category,
            )

        extracted_data = parse_result["data"]

    if category == "contract":
        extracted_data = normalize_contract_extraction(
            extracted_data
        )

    validation = validate_by_category(
        category,
        extracted_data,
    )

    if not validation:
        return {
            "success": False,
            "error": f"No validator found for category: {category}",
            "extracted_data": extracted_data,
            "compliance": {},
            "category": category,
        }

    validation_result = validation.to_dict()

    return {
        "success": True,
        "error": None,
        "extracted_data": extracted_data,
        "compliance": validation_result,
        "category": category,
        "document_hash": document_hash,
    }
