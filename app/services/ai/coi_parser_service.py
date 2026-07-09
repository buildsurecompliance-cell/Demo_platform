import json
import os

from app.services.ai.ai_service import (
    analyze_file_with_prompt,
    delete_openai_file,
    upload_file_to_openai,
)


COI_EXTRACTION_PROMPT = """
You are an insurance compliance assistant for a construction SaaS.

Analyze this Certificate of Insurance (COI).

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
  "confidence": 0.0,
  "missing_fields": [],
  "notes": ""
}

Rules:
- Return only JSON.
- No markdown.
- No explanation.
- Use empty string if a field is not found.
- Dates must be YYYY-MM-DD when possible.
- Boolean fields must be true or false.
"""


def get_mock_coi_data():

    return {
        "document_type": "Certificate of Insurance",
        "named_insured": "ABC Flooring LLC",
        "insurance_carrier": "Sample Insurance Carrier",
        "producer": "Sample Insurance Agency",
        "policy_number": "GL-123456",
        "effective_date": "2026-01-01",
        "expiration_date": "2029-01-01",
        "general_liability_limit": "1000000",
        "auto_liability_limit": "1000000",
        "workers_compensation": True,
        "umbrella_limit": "0",
        "additional_insured": True,
        "waiver_of_subrogation": True,
        "primary_non_contributory": False,
        "confidence": 0.92,
        "missing_fields": [
            "primary_non_contributory"
        ],
        "notes": "Mock COI data used for development testing."
    }


def parse_coi(file_path):

    if os.getenv("AI_MOCK_MODE", "false").lower() == "true":

        return {
            "success": True,
            "error": None,
            "data": get_mock_coi_data(),
            "raw": json.dumps(get_mock_coi_data()),
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