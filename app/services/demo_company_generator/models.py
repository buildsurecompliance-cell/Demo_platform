from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


DEMO_NOTICE = "DEMO DOCUMENT - NOT VALID FOR COMMERCIAL OR LEGAL USE"


@dataclass(frozen=True)
class DemoContact:
    name: str
    title: str
    email: str
    phone: str


@dataclass(frozen=True)
class DemoAddress:
    street: str
    city: str
    state: str
    zip_code: str

    @property
    def single_line(self):
        return f"{self.street}, {self.city}, {self.state} {self.zip_code}"

    @property
    def city_state_zip(self):
        return f"{self.city}, {self.state} {self.zip_code}"


@dataclass(frozen=True)
class DemoInsurancePolicy:
    carrier_name: str
    producer_name: str
    policy_number: str
    effective_date: date
    expiration_date: date
    general_liability_each_occurrence: int
    general_liability_aggregate: int
    auto_liability: int
    umbrella_each_occurrence: int
    workers_compensation: str
    additional_insured: bool
    waiver_of_subrogation: bool


@dataclass(frozen=True)
class DemoCompany:
    legal_name: str
    dba_name: str
    trade: str
    address: DemoAddress
    phone: str
    email: str
    website: str
    ein: str
    license_number: str
    license_expiration: date
    years_in_business: int
    employee_count: int
    primary_contact: DemoContact
    safety_contact: DemoContact
    emr: float
    naics_code: str
    generated_at: datetime

    @property
    def city(self):
        return self.address.city

    @property
    def state(self):
        return self.address.state

    @property
    def zip_code(self):
        return self.address.zip_code


@dataclass(frozen=True)
class DemoScenario:
    key: str
    label: str
    expected_status: str
    required_coverage: int
    policy: DemoInsurancePolicy
    missing_secondary_document: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DemoDocumentResult:
    filename: str
    document_type: str
    path: Path
    sha256: str
    page_count: int
    expected_extracted_fields: dict[str, Any] = field(default_factory=dict)


def dataclass_to_json(value):
    def normalize(item):
        if isinstance(item, (date, datetime)):
            return item.isoformat()

        if isinstance(item, Path):
            return str(item)

        if isinstance(item, tuple):
            return [normalize(entry) for entry in item]

        if isinstance(item, list):
            return [normalize(entry) for entry in item]

        if isinstance(item, dict):
            return {
                key: normalize(entry)
                for key, entry in item.items()
            }

        return item

    return normalize(asdict(value))


def utc_now():
    return datetime.now(timezone.utc)
