LICENSE_PROMPT = """
Analyze this business or contractor license.

Return ONLY valid JSON:

{
  "document_type": "Business License",
  "business_name": "",
  "license_number": "",
  "license_type": "",
  "issuing_state": "",
  "issue_date": "",
  "expiration_date": "",
  "status": "",
  "confidence": 0.0,
  "missing_fields": [],
  "notes": ""
}
"""