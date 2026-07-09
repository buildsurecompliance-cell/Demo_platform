SAFETY_TRAINING_PROMPT = """
Analyze this safety training certificate.

Return ONLY valid JSON:

{
  "document_type": "Safety Training",
  "person_name": "",
  "training_name": "",
  "completion_date": "",
  "expiration_date": "",
  "provider": "",
  "confidence": 0.0,
  "missing_fields": [],
  "notes": ""
}
"""