from app.services.demo_company_generator.models import DEMO_NOTICE
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
    simple_table,
    title,
    write_pdf,
)
from app.services.demo_project_generator.project_factory import schedule_rows


PROJECT_DOCUMENT_TYPES = (
    ("Prime_Contract.pdf", "Contract"),
    ("Owner_Requirements.pdf", "Owner Requirements"),
    ("Project_Scope.pdf", "Scope"),
    ("General_Conditions.pdf", "General Conditions"),
    ("Insurance_Requirements.pdf", "Owner Requirements"),
    ("Site_Logistics_Plan.pdf", "Site Logistics Plan"),
    ("Project_Schedule.pdf", "Project Schedule"),
    ("Project_Directory.pdf", "Project Directory"),
    ("Mobilization_Checklist.pdf", "Mobilization Checklist"),
    ("Safety_Requirements.pdf", "Safety Requirements"),
)


def build_project_documents(project, output_dir):
    return [
        build_prime_contract(project, output_dir),
        build_owner_requirements(project, output_dir),
        build_project_scope(project, output_dir),
        build_general_conditions(project, output_dir),
        build_insurance_requirements(project, output_dir),
        build_site_logistics_plan(project, output_dir),
        build_project_schedule(project, output_dir),
        build_project_directory(project, output_dir),
        build_mobilization_checklist(project, output_dir),
        build_safety_requirements(project, output_dir),
    ]


def build_prime_contract(project, output_dir):
    path = output_dir / "Prime_Contract.pdf"
    story = [
        title("Prime Contract"),
        notice(),
        section("Project Summary"),
        key_value_table([
            ("Project Name", project.name),
            ("Owner", project.owner),
            ("General Contractor", "BuildSure Demo General Contractor"),
            ("Contract Value", money(project.contract_value)),
            ("Start Date", format_date(project.start_date)),
            ("Substantial Completion", format_date(project.substantial_completion)),
            ("Final Completion", format_date(project.final_completion)),
            ("Project Address", project.address),
            ("City", project.city),
            ("State", project.state),
            ("Liquidated Damages", "$2,500 per calendar day after substantial completion"),
            ("Retainage", "5%"),
            ("Insurance Requirements", f"Commercial General Liability {money(project.required_insurance_limit)} each occurrence"),
        ]),
        section("Contract Documents"),
        body(
            "The Contract Documents include this Agreement, drawings, specifications, "
            "approved addenda, owner requirements, written change orders and incorporated exhibits."
        ),
        section("Contract Time"),
        body(
            f"The contractual start date is {format_date(project.start_date)}. "
            f"Substantial Completion is required no later than {format_date(project.substantial_completion)}."
        ),
    ]
    write_pdf(path, f"{project.name} Prime Contract", story, _project_brand(project))
    return document_result(
        path,
        "Contract",
        {
            "project_name": project.name,
            "contract_value": project.contract_value,
            "start_date": format_date(project.start_date),
            "end_date": format_date(project.substantial_completion),
            "required_coverage": project.required_insurance_limit,
        },
    )


def build_owner_requirements(project, output_dir):
    path = output_dir / "Owner_Requirements.pdf"
    sections = (
        "Insurance Requirements",
        "Additional Insured",
        "Waiver of Subrogation",
        "Required Coverages",
        "Safety",
        "Reporting",
        "Schedule",
        "Quality",
        "Closeout",
    )
    pages = []
    for heading in sections:
        pages.append([
            title(f"Owner Requirements - {heading}"),
            notice(),
            section(heading),
            body(
                f"{project.owner} requires documented compliance for {project.name}. "
                f"This section is fictional and used for BuildSure parsing and readiness demos."
            ),
            *paragraph_list([
                "Submit required documents before mobilization.",
                "Maintain current insurance and safety documentation.",
                "Report status changes to the General Contractor.",
            ]),
        ])
    write_pdf(path, f"{project.name} Owner Requirements", [], _project_brand(project), pages=pages)
    return document_result(path, "Owner Requirements", {"required_coverage": project.required_insurance_limit})


def build_project_scope(project, output_dir):
    path = output_dir / "Project_Scope.pdf"
    story = [
        title("Project Scope"),
        notice(),
        body(project.description),
        section("CSI Divisions"),
        *paragraph_list(project.csi_divisions),
        section("Scope Notes"),
        body("Trade scopes must coordinate document compliance before work starts on site."),
    ]
    write_pdf(path, f"{project.name} Project Scope", story, _project_brand(project))
    return document_result(path, "Scope", {"project_name": project.name})


def build_general_conditions(project, output_dir):
    path = output_dir / "General_Conditions.pdf"
    story = [
        title("General Conditions"),
        notice(),
        *paragraph_list([
            "Subcontractors must follow site access rules.",
            "Compliance documents must remain current.",
            "Daily reports and coordination meetings are required.",
            "Work must comply with approved drawings and specifications.",
        ]),
    ]
    write_pdf(path, f"{project.name} General Conditions", story, _project_brand(project))
    return document_result(path, "General Conditions", {"project_name": project.name})


def build_insurance_requirements(project, output_dir):
    path = output_dir / "Insurance_Requirements.pdf"
    story = [
        title("Insurance Requirements"),
        notice(),
        key_value_table([
            ("Commercial General Liability", f"{money(project.required_insurance_limit)} each occurrence"),
            ("Additional Insured", "Required"),
            ("Waiver of Subrogation", "Required"),
            ("Automobile Liability", "$1,000,000 combined single limit"),
            ("Workers Compensation", "Statutory"),
        ]),
    ]
    write_pdf(path, f"{project.name} Insurance Requirements", story, _project_brand(project))
    return document_result(path, "Owner Requirements", {"required_coverage": project.required_insurance_limit})


def build_site_logistics_plan(project, output_dir):
    path = output_dir / "Site_Logistics_Plan.pdf"
    story = [
        title("Site Logistics Plan"),
        notice(),
        key_value_table([
            ("Delivery Hours", "7:00 AM - 3:30 PM weekdays"),
            ("Parking", "Designated subcontractor lot only"),
            ("Laydown Area", "Northwest fenced zone"),
            ("Gate Access", "Gate 2 with badge check-in"),
            ("Tower Crane", "Coordinate picks 48 hours in advance"),
            ("Emergency Contacts", f"{project.superintendent}, {project.safety_manager}"),
            ("Site Rules", "PPE, badge and orientation required before entry"),
        ]),
    ]
    write_pdf(path, f"{project.name} Site Logistics Plan", story, _project_brand(project))
    return document_result(path, "Site Logistics Plan", {"project_name": project.name})


def build_project_schedule(project, output_dir):
    path = output_dir / "Project_Schedule.pdf"
    story = [
        title("Project Schedule"),
        notice(),
        simple_table(
            ["Phase", "Start", "Finish"],
            [
                (phase, format_date(start), format_date(end))
                for phase, start, end in schedule_rows(project)
            ],
        ),
    ]
    write_pdf(path, f"{project.name} Project Schedule", story, _project_brand(project))
    return document_result(path, "Project Schedule", {"project_name": project.name})


def build_project_directory(project, output_dir):
    path = output_dir / "Project_Directory.pdf"
    story = [
        title("Project Directory"),
        notice(),
        key_value_table([
            ("Owner", f"{project.owner} | owner-{project.key}@demo-buildsure.com | (555) 010-1000"),
            ("Architect", f"{project.architect} | architect-{project.key}@demo-buildsure.com | (555) 010-1001"),
            ("GC", "BuildSure Demo General Contractor | gc@demo-buildsure.com | (555) 010-1002"),
            ("PM", f"{project.project_manager} | pm-{project.key}@demo-buildsure.com | (555) 010-1003"),
            ("Superintendent", f"{project.superintendent} | super-{project.key}@demo-buildsure.com | (555) 010-1004"),
            ("Safety Manager", f"{project.safety_manager} | safety-{project.key}@demo-buildsure.com | (555) 010-1005"),
            ("MEP Coordinator", f"{project.mep_coordinator} | mep-{project.key}@demo-buildsure.com | (555) 010-1006"),
        ]),
    ]
    write_pdf(path, f"{project.name} Project Directory", story, _project_brand(project))
    return document_result(path, "Project Directory", {"project_name": project.name})


def build_mobilization_checklist(project, output_dir):
    path = output_dir / "Mobilization_Checklist.pdf"
    story = [
        title("Mobilization Checklist"),
        notice(),
        simple_table(
            ["Item", "Required", "Notes"],
            [
                ("COI", "Yes", "Validated before work starts"),
                ("Executed Contract", "Yes", "Signed subcontract agreement"),
                ("Safety Orientation", "Yes", "Before site access"),
                ("Badge", "Yes", "Issued by site team"),
                ("Drug Test", "As Required", "Trade-specific"),
                ("Equipment Inspection", "Yes", "Before equipment use"),
                ("Lift Certifications", "As Required", "For lift operators"),
                ("Daily Reports", "Yes", "Submitted through GC process"),
            ],
        ),
    ]
    write_pdf(path, f"{project.name} Mobilization Checklist", story, _project_brand(project))
    return document_result(path, "Mobilization Checklist", {"project_name": project.name})


def build_safety_requirements(project, output_dir):
    path = output_dir / "Safety_Requirements.pdf"
    story = [
        title("Safety Requirements"),
        notice(),
        *paragraph_list([
            "Site orientation is required before mobilization.",
            "Task-specific pre-planning is required for high-risk activities.",
            "Incident reporting must occur immediately.",
            "Concrete, steel, roofing, MEP and finish trades must maintain current compliance documents.",
        ]),
        gap(),
        body(DEMO_NOTICE),
    ]
    write_pdf(path, f"{project.name} Safety Requirements", story, _project_brand(project))
    return document_result(path, "Safety Requirements", {"project_name": project.name})


class _ProjectBrand:
    def __init__(self, project):
        self.legal_name = "BuildSure Demo General Contractor"
        self.email = f"project-{project.key}@demo-buildsure.com"


def _project_brand(project):
    return _ProjectBrand(project)
