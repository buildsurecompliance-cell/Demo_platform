from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProjectRequirements:

    general_liability: int = 1000000
    auto_liability: int = 1000000
    umbrella: int = 0

    workers_comp: bool = True
    additional_insured: bool = True
    waiver_of_subrogation: bool = True
    primary_non_contributory: bool = False

    coi_must_cover_project_duration: bool = True


@dataclass
class ComplianceIssue:

    field: str
    message: str
    severity: str = "blocking"


@dataclass
class ComplianceDecision:

    status: str
    score: int
    issues: list[ComplianceIssue] = field(default_factory=list)
    warnings: list[ComplianceIssue] = field(default_factory=list)
    extracted_data: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ready(self):
        return self.status == "Ready"

    @property
    def is_blocked(self):
        return self.status == "Blocked"

    @property
    def is_pending(self):
        return self.status == "Pending"