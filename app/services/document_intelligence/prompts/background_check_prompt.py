BACKGROUND_CHECK_PROMPT = """
Analyze this background check document.

Return ONLY valid JSON:

{
  "document_type": "Background Check",
  "person_name": "",
  "completed_date": "",
  "status": "",
  "provider": "",
  "confidence": 0.0,
  "missing_fields": [],
  "notes": ""
}

Rules:
- status should be "clear", "passed", "failed", "pending", or empty string.
"""