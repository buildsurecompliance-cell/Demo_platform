from app.services.demo_company_generator.models import DEMO_NOTICE
from app.services.demo_company_generator.pdf_builder import (
    body,
    document_result,
    format_date,
    gap,
    key_value_table,
    money,
    notice,
    section,
    simple_table,
    title,
    write_pdf,
)


def build_coi(company, scenario, output_dir):
    policy = scenario.policy
    path = output_dir / "Certificate_of_Insurance.pdf"
    fields = {
        "expiration_date": format_date(policy.expiration_date),
        "general_liability_limit": policy.general_liability_each_occurrence,
        "insurance_carrier": policy.carrier_name,
        "policy_number": policy.policy_number,
        "email": company.email,
        "phone": company.phone,
        "additional_insured": policy.additional_insured,
        "waiver_of_subrogation": policy.waiver_of_subrogation,
    }

    story = [
        title("Certificate of Insurance"),
        notice(),
        gap(),
        key_value_table(
            [
                ("Producer", policy.producer_name),
                ("Insured", f"{company.legal_name}<br/>{company.address.single_line}"),
                ("Insurers Affording Coverage", policy.carrier_name),
                ("Policy Number", policy.policy_number),
                ("Policy EFF", format_date(policy.effective_date)),
                ("Policy EXP", format_date(policy.expiration_date)),
                ("TRADE / OPERATIONS", company.trade),
                ("SUBCONTRACTOR EMAIL", company.email),
                ("SUBCONTRACTOR PHONE", company.phone),
            ]
        ),
        section("Coverage Summary"),
        simple_table(
            ["Coverage", "Limit", "Additional Insured", "Waiver of Subrogation"],
            [
                [
                    "Commercial General Liability",
                    f"Each Occurrence {money(policy.general_liability_each_occurrence)}; General Aggregate {money(policy.general_liability_aggregate)}",
                    "Yes" if policy.additional_insured else "No",
                    "Yes" if policy.waiver_of_subrogation else "No",
                ],
                [
                    "Automobile Liability",
                    f"Combined Single Limit {money(policy.auto_liability)}",
                    "No",
                    "No",
                ],
                [
                    "Umbrella Liability",
                    f"Each Occurrence {money(policy.umbrella_each_occurrence)}",
                    "No",
                    "No",
                ],
                [
                    "Workers Compensation",
                    policy.workers_compensation,
                    "No",
                    "No",
                ],
            ],
        ),
        section("Description of Operations"),
        body(
            f"{company.trade} operations including mobilization, layout coordination, "
            "foundations, slab-on-grade, "
            "equipment pads, curbs, housekeeping pads, placement, finishing and cleanup "
            "for Summit Distribution Center. Certificate holder is included as additional "
            "insured where required by written contract."
        ),
        section("Certificate Holder"),
        body(
            "Summit Distribution Center<br/>"
            "c/o BuildSure Demo General Contractor<br/>"
            "Dallas, TX"
        ),
        section("Authorized Representative"),
        body(
            "Jordan Lee, Demo Risk Advisor<br/>"
            "Bluebonnet Risk Advisors Demo Agency"
        ),
        gap(),
        body(DEMO_NOTICE),
    ]

    write_pdf(path, "Certificate of Insurance", story, company)
    return document_result(path, "COI", fields)
