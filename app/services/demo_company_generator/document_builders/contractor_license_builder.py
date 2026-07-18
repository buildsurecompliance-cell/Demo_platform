from app.services.demo_company_generator.pdf_builder import (
    body,
    document_result,
    format_date,
    gap,
    key_value_table,
    notice,
    title,
    write_pdf,
)


def build_contractor_license(company, scenario, output_dir):
    path = output_dir / "Contractor_License.pdf"
    story = [
        title("Contractor License - Demonstration"),
        notice(),
        gap(),
        key_value_table(
            [
                ("License Number", company.license_number),
                ("Company", company.legal_name),
                ("Trade Classification", company.trade),
                ("Issue Date", "01/15/2024"),
                ("Expiration Date", format_date(company.license_expiration)),
                ("Status", "Active" if scenario.key != "blocked" else "Review Required"),
                ("Issuing Authority", "Texas Construction Demo Registry"),
                ("Demo Verification ID", f"DEMO-{company.license_number}"),
            ]
        ),
        gap(),
        body(
            "This document is a fictional demonstration license and does not imitate "
            "or replace any government-issued credential."
        ),
    ]
    write_pdf(path, "Contractor License", story, company)
    return document_result(
        path,
        "Contractor License",
        {
            "license_number": company.license_number,
            "expiration_date": format_date(company.license_expiration),
        },
    )
