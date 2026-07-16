CONTRACT_PROMPT = """
Extract project contract fields as strict JSON.

Use only project contract terms:
- project_name: project title/name from the contract.
- contract_value: contract sum, contract price, or contract value only.
  Do not use insurance limits, retainage, allowances, liquidated damages,
  bond amounts, or alternates as contract_value.
- start_date: project start/commencement date only when clearly identified.
  Do not use signature, issue, certificate, or generic effective dates unless
  the contract clearly states they are the project start date.
- end_date: project end/completion/substantial completion date only when
  clearly identified.
- required_coverage: minimum General Liability per-occurrence requirement
  only. Do not use umbrella, automobile liability, workers compensation,
  aggregate limits, or other insurance lines.

Return exactly these keys:
- document_type: "contract"
- project_name: string or null
- contract_value: numeric value or null
- start_date: ISO YYYY-MM-DD string, MM/DD/YYYY string, or null
- end_date: ISO YYYY-MM-DD string, MM/DD/YYYY string, or null
- required_coverage: numeric value, common shorthand such as "2M", or null
- confidence: number from 0 to 1
- field_confidence: object with project_name, contract_value, start_date,
  end_date, required_coverage numbers from 0 to 1
- notes: array of short strings

Do not decide compliance readiness. Do not add entities.
"""
