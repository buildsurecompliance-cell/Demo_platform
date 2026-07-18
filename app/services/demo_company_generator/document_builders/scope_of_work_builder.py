from app.services.demo_company_generator.pdf_builder import (
    body,
    document_result,
    gap,
    notice,
    paragraph_list,
    section,
    title,
    write_pdf,
)


def build_scope_of_work(company, scenario, output_dir):
    path = output_dir / "Scope_of_Work.pdf"
    story = [
        title("Scope of Work - Summit Distribution Center"),
        notice(),
        gap(),
        body(
            f"{company.legal_name} will provide concrete trade work for Summit "
            "Distribution Center under a fictional demonstration scope."
        ),
        section("Included Work"),
        *paragraph_list([
            "Mobilization and site coordination",
            "Layout coordination with the General Contractor",
            "Foundations and grade beams",
            "Slab-on-grade placement and finishing",
            "Equipment pads and housekeeping pads",
            "Curbs, ramps and concrete repairs",
            "Concrete placement, finishing and curing",
            "Daily cleanup of concrete work areas",
            "Submittals for mix designs and product data",
            "Schedule coordination with adjacent trades",
            "Safety requirements and pre-task planning",
        ]),
        section("Exclusions"),
        *paragraph_list([
            "Structural steel embeds not specifically shown in concrete documents",
            "Testing laboratory services",
            "Permanent dewatering",
            "Design services",
        ]),
    ]
    write_pdf(path, "Scope of Work", story, company)
    return document_result(
        path,
        "Scope",
        {"project": "Summit Distribution Center", "trade": company.trade},
    )
