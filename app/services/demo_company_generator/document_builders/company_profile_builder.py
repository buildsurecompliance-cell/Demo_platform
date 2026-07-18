from app.services.demo_company_generator.pdf_builder import (
    body,
    document_result,
    gap,
    key_value_table,
    notice,
    paragraph_list,
    section,
    title,
    write_pdf,
)


def build_company_profile(company, scenario, output_dir):
    path = output_dir / "Company_Profile.pdf"
    pages = [
        [
            title("Company Profile"),
            notice(),
            section("About"),
            body(
                f"{company.legal_name} is a fictional Dallas concrete subcontractor "
                f"created for BuildSure demonstrations. The company profile reflects "
                f"{company.years_in_business} years in business and {company.employee_count} employees."
            ),
            section("Services"),
            *paragraph_list([
                "Foundations and footings",
                "Slab-on-grade placement",
                "Equipment pads and housekeeping pads",
                "Curbs, ramps and concrete repairs",
            ]),
        ],
        [
            title("Markets Served"),
            notice(),
            *paragraph_list([
                "Distribution and logistics facilities",
                "Medical and institutional projects",
                "Commercial tenant improvements",
                "Light industrial construction",
            ]),
            section("Leadership"),
            key_value_table([
                ("President", company.primary_contact.name),
                ("Safety Director", company.safety_contact.name),
            ]),
        ],
        [
            title("Equipment and Safety"),
            notice(),
            section("Equipment"),
            *paragraph_list([
                "Laser screeds and power trowels",
                "Concrete pumps coordinated through approved partners",
                "Curing equipment and temporary protection materials",
                "Fleet trucks and layout tools",
            ]),
            section("Safety"),
            body(f"Current demonstration EMR: {company.emr:.2f}."),
        ],
        [
            title("Selected Projects and Contact"),
            notice(),
            section("Selected Projects"),
            *paragraph_list([
                "Summit Distribution Center - Dallas, TX",
                "Oakridge Civic Plaza - Austin, TX",
                "Harbor Point Renovation - Houston, TX",
            ]),
            section("Contact Information"),
            key_value_table([
                ("Email", company.email),
                ("Phone", company.phone),
                ("Website", company.website),
                ("Address", company.address.single_line),
            ]),
        ],
    ]
    write_pdf(path, "Company Profile", [], company, pages=pages)
    return document_result(path, "Company Profile", {"legal_name": company.legal_name})
