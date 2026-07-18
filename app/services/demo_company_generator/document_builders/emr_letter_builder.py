from app.services.demo_company_generator.pdf_builder import (
    body,
    document_result,
    format_date,
    gap,
    key_value_table,
    notice,
    section,
    title,
    write_pdf,
)


def build_emr_letter(company, scenario, output_dir):
    path = output_dir / "EMR_Verification_Letter.pdf"
    story = [
        title("EMR Verification Letter"),
        notice(),
        gap(),
        body(
            f"{scenario.policy.carrier_name} confirms, for demonstration purposes only, "
            f"that {company.legal_name} is listed with an Experience Modification Rate "
            f"of {company.emr:.2f} for the policy period shown below."
        ),
        section("Verification Details"),
        key_value_table(
            [
                ("Company", company.legal_name),
                ("Policy Period", f"{format_date(scenario.policy.effective_date)} - {format_date(scenario.policy.expiration_date)}"),
                ("EMR", f"{company.emr:.2f}"),
                ("Date Issued", format_date(scenario.policy.effective_date)),
                ("Producer Contact", scenario.policy.producer_name),
            ]
        ),
        section("Signature"),
        body(
            "Jordan Lee<br/>Senior Risk Advisor<br/>"
            "Bluebonnet Risk Advisors Demo Agency"
        ),
    ]
    write_pdf(path, "EMR Verification Letter", story, company)
    return document_result(path, "EMR Letter", {"emr": company.emr})
