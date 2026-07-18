from app.services.demo_company_generator.pdf_builder import (
    body,
    document_result,
    notice,
    paragraph_list,
    section,
    title,
    write_pdf,
)


SAFETY_SECTIONS = (
    ("Safety Policy", ["Management supports safe planning before work begins.", "Crews stop work when conditions are unsafe."]),
    ("Responsibilities", ["Supervisors communicate hazards daily.", "Employees report unsafe conditions promptly."]),
    ("New Hire Orientation", ["New employees review company rules before site work.", "Orientation records are kept by the safety contact."]),
    ("Personal Protective Equipment", ["Hard hats, eye protection, gloves and high-visibility apparel are required.", "Task-specific PPE is assigned for concrete placement."]),
    ("Concrete Operations", ["Placement plans address access, pumping, finishing and curing.", "Crews maintain clear paths around trucks and equipment."]),
    ("Silica Exposure Control", ["Wet methods and dust controls are used for cutting or grinding.", "Respiratory protection is assigned when controls require it."]),
    ("Excavation and Trenching", ["Excavations are evaluated before entry.", "Spoils and equipment are kept back from edges."]),
    ("Fall Protection", ["Open edges and elevated work areas require approved controls.", "Employees inspect fall protection equipment before use."]),
    ("Electrical Safety", ["Temporary power is protected and inspected.", "Damaged cords are removed from service."]),
    ("Lockout/Tagout", ["Energy sources are controlled before maintenance.", "Only authorized employees remove controls."]),
    ("Equipment and Vehicle Safety", ["Operators inspect equipment before use.", "Spotters are used where visibility is limited."]),
    ("Hazard Communication", ["Safety data sheets are available to employees.", "Containers are labeled according to site requirements."]),
    ("Incident Reporting", ["Incidents and near misses are reported immediately.", "Corrective actions are documented."]),
    ("Emergency Response", ["Emergency contacts are posted for each project.", "Crews maintain access for first responders."]),
    ("Drug-Free Workplace", ["The company maintains a drug-free workplace policy.", "Testing procedures are administered by approved providers."]),
    ("Training Records", ["Training completion is documented.", "Records are available for compliance review."]),
    ("Acknowledgement", ["Employees acknowledge receipt of this demonstration manual.", "This document is not legal advice."]),
)


def build_safety_manual(company, scenario, output_dir):
    path = output_dir / "Safety_Manual.pdf"
    pages = []
    for heading, bullets in SAFETY_SECTIONS:
        pages.append(
            [
                title(f"Safety Manual - {heading}"),
                notice(),
                section(heading),
                body(
                    f"{company.legal_name} uses this fictional section to demonstrate "
                    "how a safety manual can be stored, reviewed and searched in BuildSure."
                ),
                *paragraph_list(bullets),
            ]
        )

    write_pdf(path, "Safety Manual", [], company, pages=pages)
    return document_result(
        path,
        "Safety Manual",
        {"minimum_pages": 12, "sections": len(SAFETY_SECTIONS)},
    )
