from app.services.demo_company_generator.pdf_builder import (
    body,
    document_result,
    format_date,
    gap,
    key_value_table,
    money,
    notice,
    paragraph_list,
    section,
    title,
    write_pdf,
)


SUBCONTRACT_AMOUNT = 2_450_000


def build_subcontract_agreement(company, scenario, output_dir):
    path = output_dir / "Subcontract_Agreement.pdf"
    pages = [
        [
            title("Subcontract Agreement"),
            notice(),
            key_value_table([
                ("General Contractor", "BuildSure Demo General Contractor"),
                ("Subcontractor", company.legal_name),
                ("Project", "Summit Distribution Center"),
                ("Contract Amount", money(SUBCONTRACT_AMOUNT)),
                ("Agreement Date", format_date(scenario.policy.effective_date)),
            ]),
            section("Scope"),
            body("Subcontractor shall provide concrete labor, supervision, equipment and materials for the project scope."),
        ],
        [
            title("Schedule, Insurance and Safety"),
            notice(),
            section("Schedule"),
            body("Work shall be coordinated with the General Contractor's published project schedule."),
            section("Insurance"),
            body(f"Commercial General Liability Each Occurrence: {money(scenario.policy.general_liability_each_occurrence)}."),
            section("Safety"),
            body("Subcontractor shall comply with project safety requirements and maintain training records."),
        ],
        [
            title("Commercial Terms"),
            notice(),
            section("Change Orders"),
            body("Changes must be documented in writing before extra work proceeds."),
            section("Payment"),
            body("Payment applications are submitted monthly with required compliance documents."),
            section("Indemnification"),
            body("The parties agree to standard risk allocation terms for demonstration purposes only."),
            section("Termination"),
            body("Either party may terminate according to written notice provisions in this fictional agreement."),
        ],
        [
            title("Signatures"),
            notice(),
            *paragraph_list([
                "BuildSure Demo General Contractor: __________________________",
                f"{company.legal_name}: __________________________",
                "Date: __________________________",
            ]),
            gap(),
            body("This agreement is fictional and has no legal validity."),
        ],
    ]
    write_pdf(path, "Subcontract Agreement", [], company, pages=pages)
    return document_result(
        path,
        "Subcontract Agreement",
        {
            "project": "Summit Distribution Center",
            "contract_amount": SUBCONTRACT_AMOUNT,
        },
    )
