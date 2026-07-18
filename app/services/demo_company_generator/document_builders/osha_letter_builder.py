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


def build_osha_letter(company, scenario, output_dir):
    path = output_dir / "OSHA_Compliance_Letter.pdf"
    story = [
        title("Safety Program and OSHA Compliance Letter"),
        notice(),
        gap(),
        body(
            f"{company.legal_name} maintains an active safety program for concrete "
            "operations and requires employees to follow applicable workplace safety "
            "procedures. This letter does not represent a government certification."
        ),
        section("Program Summary"),
        *paragraph_list(
            [
                "Employees receive new-hire safety orientation and task-specific training.",
                "Incident records are reviewed by the safety contact for corrective action.",
                "Concrete crews follow silica, excavation, fall protection and PPE controls.",
                "Safety meetings are documented for demonstration testing.",
            ]
        ),
        section("Responsible Contact"),
        key_value_table(
            [
                ("Safety Contact", company.safety_contact.name),
                ("Title", company.safety_contact.title),
                ("Email", company.safety_contact.email),
                ("Phone", company.safety_contact.phone),
            ]
        ),
    ]
    write_pdf(path, "OSHA Compliance Letter", story, company)
    return document_result(
        path,
        "OSHA Letter",
        {"safety_contact": company.safety_contact.name},
    )
