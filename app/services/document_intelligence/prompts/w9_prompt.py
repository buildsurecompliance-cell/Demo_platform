W9_PROMPT = """
Analyze this W-9 form.

Return ONLY valid JSON:

{
  "document_type": "W-9",
  "legal_name": "",
  "business_name": "",
  "tax_classification": "",
  "tax_id_last4": "",
  "address": "",
  "signed": false,
  "signature_date": "",
  "confidence": 0.0,
  "missing_fields": [],
  "notes": ""
}
"""