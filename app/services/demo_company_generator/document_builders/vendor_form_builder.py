from app.services.demo_company_generator.pdf_builder import (
    body,
    document_result,
    gap,
    key_value_table,
    notice,
    section,
    title,
    write_pdf,
)


def build_vendor_form(company, scenario, output_dir):
    path = output_dir / "Vendor_Information_Form.pdf"
    story = [
        title("Vendor Information Form"),
        notice(),
        gap(),
        section("Company"),
        key_value_table(
            [
                ("Legal Name", company.legal_name),
                ("DBA", company.dba_name),
                ("Trade", company.trade),
                ("Address", company.address.single_line),
                ("Phone", company.phone),
                ("Email", company.email),
                ("Website", company.website),
                ("NAICS Code", company.naics_code),
                ("Years in Business", company.years_in_business),
                ("Employees", company.employee_count),
            ]
        ),
        section("Contacts"),
        key_value_table(
            [
                ("Primary Contact", f"{company.primary_contact.name}, {company.primary_contact.title}"),
                ("Safety Contact", f"{company.safety_contact.name}, {company.safety_contact.title}"),
            ]
        ),
        body("Banking and payment fields are intentionally omitted from this demo package."),
    ]
    write_pdf(path, "Vendor Information Form", story, company)
    return document_result(
        path,
        "Vendor Form",
        {"legal_name": company.legal_name, "trade": company.trade},
    )
