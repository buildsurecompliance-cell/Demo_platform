import json
import os

from app.services.ai.ai_service import (
    analyze_file_with_prompt,
    delete_openai_file,
    upload_file_to_openai,
)
from app.services.document_intelligence.engine import (
    _parse_coi_in_mock_mode,
)


COI_EXTRACTION_PROMPT = """
You are an insurance compliance assistant for a construction SaaS.

Analyze this Certificate of Insurance (COI).

Extract values only from the supplied document. Never copy values from
examples. Return empty values for fields not present.

Use POLICY EXP for expiration_date and COMMERCIAL GENERAL LIABILITY / EACH
OCCURRENCE for general_liability_limit. Do not use certificate issue dates,
effective dates, aggregate limits, auto, umbrella, workers compensation, or
damage to rented premises as substitutes.

Extract insured/subcontractor email and phone when present. Do not use
producer, broker, insurance agency, carrier, insurer, or certificate holder
contact details as subcontractor contact details.

Return ONLY valid JSON with this exact structure:

{
  "document_type": "Certificate of Insurance",
  "named_insured": "",
  "insurance_carrier": "",
  "producer": "",
  "policy_number": "",
  "effective_date": "",
  "expiration_date": "",
  "general_liability_limit": "",
  "auto_liability_limit": "",
  "workers_compensation": false,
  "umbrella_limit": "",
  "additional_insured": false,
  "waiver_of_subrogation": false,
  "primary_non_contributory": false,
  "trade": "",
  "email": "",
  "phone": "",
  "confidence": 0.0,
  "missing_fields": [],
  "notes": ""
}
"""


def parse_coi(file_path):

    if os.getenv("AI_MOCK_MODE", "false").lower() == "true":
        parse_result = _parse_coi_in_mock_mode(file_path)

        return {
            "success": parse_result["success"],
            "error": parse_result["error"],
            "data": parse_result["data"],
            "raw": json.dumps(parse_result["data"] or {}),
        }

    uploaded_file = None

    try:
        uploaded_file = upload_file_to_openai(file_path)

        raw_result = analyze_file_with_prompt(
            uploaded_file.id,
            COI_EXTRACTION_PROMPT
        )

        data = json.loads(raw_result)

        return {
            "success": True,
            "error": None,
            "data": data,
            "raw": raw_result,
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e),
            "data": None,
            "raw": None,
        }

    finally:

        if uploaded_file:
            delete_openai_file(uploaded_file.id)
