from dataclasses import asdict, dataclass
from datetime import date, timedelta
from random import Random


@dataclass(frozen=True)
class DemoProject:
    key: str
    name: str
    owner: str
    architect: str
    project_manager: str
    superintendent: str
    safety_manager: str
    mep_coordinator: str
    address: str
    city: str
    state: str
    zip_code: str
    contract_value: int
    start_date: date
    substantial_completion: date
    final_completion: date
    required_insurance_limit: int
    status: str
    description: str
    csi_divisions: tuple[str, ...]


PROJECT_PRESETS = (
    DemoProject(
        key="summit-distribution-center",
        name="Summit Distribution Center",
        owner="Summit Logistics Holdings LLC",
        architect="Mason Bell Architecture",
        project_manager="Rebecca Stone",
        superintendent="Luis Herrera",
        safety_manager="Dana Fields",
        mep_coordinator="Owen Patel",
        address="4200 Logistics Parkway",
        city="Dallas",
        state="TX",
        zip_code="75261",
        contract_value=14_500_000,
        start_date=date(2026, 3, 1),
        substantial_completion=date(2027, 10, 31),
        final_completion=date(2027, 12, 15),
        required_insurance_limit=2_000_000,
        status="Active",
        description="New regional distribution facility with warehouse, office and loading operations.",
        csi_divisions=("03 Concrete", "05 Metals", "07 Thermal", "21 Fire Suppression", "26 Electrical"),
    ),
    DemoProject(
        key="westside-medical-center",
        name="Westside Medical Center",
        owner="Westside Health Partners",
        architect="Cedar Hill Design Group",
        project_manager="Natalie Brooks",
        superintendent="Marcus Reed",
        safety_manager="Helen Ortiz",
        mep_coordinator="Priya Shah",
        address="8701 Medical Center Drive",
        city="Austin",
        state="TX",
        zip_code="78735",
        contract_value=32_000_000,
        start_date=date(2026, 5, 1),
        substantial_completion=date(2028, 12, 31),
        final_completion=date(2029, 2, 15),
        required_insurance_limit=5_000_000,
        status="Preconstruction",
        description="Medical office and outpatient procedure expansion with strict site access requirements.",
        csi_divisions=("03 Concrete", "07 Thermal", "09 Finishes", "22 Plumbing", "23 HVAC", "26 Electrical"),
    ),
    DemoProject(
        key="riverside-office-building",
        name="Riverside Office Building",
        owner="Riverside Development Group",
        architect="Harper Lane Studio",
        project_manager="Evan Miles",
        superintendent="Carlos Vega",
        safety_manager="Monica Grant",
        mep_coordinator="Sarah Kim",
        address="1110 Bayou Bend Avenue",
        city="Houston",
        state="TX",
        zip_code="77019",
        contract_value=8_200_000,
        start_date=date(2026, 2, 10),
        substantial_completion=date(2027, 6, 30),
        final_completion=date(2027, 8, 1),
        required_insurance_limit=2_000_000,
        status="Active",
        description="Mid-rise office renovation and shell improvements near the river district.",
        csi_divisions=("03 Concrete", "06 Wood", "08 Openings", "09 Finishes", "26 Electrical"),
    ),
    DemoProject(
        key="northgate-logistics-center",
        name="Northgate Logistics Center",
        owner="Northgate Industrial Partners",
        architect="Lineweight Architects",
        project_manager="Jenna Walsh",
        superintendent="Diego Flores",
        safety_manager="Victor Chen",
        mep_coordinator="Tara Simmons",
        address="2600 Northgate Loop",
        city="Fort Worth",
        state="TX",
        zip_code="76177",
        contract_value=18_750_000,
        start_date=date(2026, 6, 15),
        substantial_completion=date(2028, 3, 31),
        final_completion=date(2028, 5, 15),
        required_insurance_limit=2_000_000,
        status="Planning",
        description="Logistics center with concrete paving, dock equipment and high-bay warehouse areas.",
        csi_divisions=("03 Concrete", "05 Metals", "07 Thermal", "08 Openings", "32 Exterior Improvements"),
    ),
    DemoProject(
        key="metro-commerce-center",
        name="Metro Commerce Center",
        owner="Metro Commerce Ventures",
        architect="Alamo Urban Design",
        project_manager="Rachel Dunn",
        superintendent="Andre Collins",
        safety_manager="Nora Bennett",
        mep_coordinator="Samir Nadeem",
        address="1900 Commerce Crossing",
        city="San Antonio",
        state="TX",
        zip_code="78205",
        contract_value=24_300_000,
        start_date=date(2026, 8, 1),
        substantial_completion=date(2029, 1, 15),
        final_completion=date(2029, 3, 1),
        required_insurance_limit=5_000_000,
        status="Preconstruction",
        description="Mixed commercial center with retail shell space, parking improvements and MEP coordination.",
        csi_divisions=("03 Concrete", "04 Masonry", "07 Thermal", "09 Finishes", "22 Plumbing", "26 Electrical"),
    ),
)


def list_demo_projects(seed=123):
    random = Random(seed)
    projects = list(PROJECT_PRESETS)
    random.shuffle(projects)
    return sorted(projects, key=lambda project: project.key)


def project_to_json(project):
    def normalize(value):
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, tuple):
            return list(value)
        return value

    return {
        key: normalize(value)
        for key, value in asdict(project).items()
    }


def schedule_rows(project):
    milestones = (
        "Mobilization",
        "Earthwork",
        "Foundations",
        "Structure",
        "Envelope",
        "MEP Rough-In",
        "Drywall",
        "Flooring",
        "Ceilings",
        "Finishes",
        "Commissioning",
        "Closeout",
    )
    start = project.start_date
    rows = []
    for index, milestone in enumerate(milestones):
        item_start = start + timedelta(days=index * 45)
        item_end = item_start + timedelta(days=35)
        rows.append((milestone, item_start, item_end))
    return rows
