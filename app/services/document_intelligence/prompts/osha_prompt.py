OSHA_PROMPT = """
Analyze this OSHA training document or OSHA card.

Return ONLY valid JSON:

{
  "document_type": "OSHA",
  "holder_name": "",
  "training_type": "",
  "completion_date": "",
  "trainer": "",
  "card_number": "",
  "confidence": 0.0,
  "missing_fields": [],
  "notes": ""
}
"""