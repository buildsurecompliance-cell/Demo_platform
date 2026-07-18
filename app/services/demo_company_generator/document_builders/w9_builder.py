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


def build_w9(company, scenario, output_dir):
    path = output_dir / "W9_Demo.pdf"
    masked_ein = f"XX-XXX{company.ein[-4:]}"
    story = [
        title("Request for Taxpayer Information"),
        notice(),
        gap(),
        body(
            "This demonstration document is inspired by common vendor onboarding "
            "requirements. It is not an IRS form and must not be used for tax filing."
        ),
        section("Business Information"),
        key_value_table(
            [
                ("Legal Name", company.legal_name),
                ("Business Name", company.dba_name),
                ("Federal Tax Classification", "Limited Liability Company"),
                ("Address", company.address.street),
                ("City, State, ZIP", company.address.city_state_zip),
                ("Taxpayer Identification Number", masked_ein),
            ]
        ),
        section("Certification"),
        body(
            "The undersigned certifies that the above information is accurate for "
            "demonstration testing only."
        ),
        key_value_table(
            [
                ("Signature", company.primary_contact.name),
                ("Title", company.primary_contact.title),
                ("Date", format_date(scenario.policy.effective_date)),
            ]
        ),
    ]
    write_pdf(path, "W-9 Demo", story, company)
    return document_result(
        path,
        "W9",
        {
            "legal_name": company.legal_name,
            "tax_id_last4": company.ein[-4:],
        },
    )
