DRUG_TEST_PROMPT = """
Analyze this drug test or drug screening document.

Return ONLY valid JSON:

{
  "document_type": "Drug Test",
  "person_name": "",
  "test_date": "",
  "result": "",
  "testing_company": "",
  "confidence": 0.0,
  "missing_fields": [],
  "notes": ""
}

Rules:
- result should be "negative", "positive", "inconclusive", or empty string.
"""