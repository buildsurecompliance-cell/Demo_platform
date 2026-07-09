from dataclasses import dataclass, field


@dataclass
class ComplianceProfile:
    name: str
    description: str = ""

    required_documents: list[str] = field(default_factory=list)
    safety_requirements: list[str] = field(default_factory=list)

    general_liability: int = 1000000
    auto_liability: int = 1000000
    umbrella: int = 0
    workers_comp: bool = True

    additional_insured: bool = True
    waiver_of_subrogation: bool = True
    primary_non_contributory: bool = False

    renewal_required_days: int = 180
    pending_renewal_days: int = 30

    def to_dict(self):
        return {
            "name": self.name,
            "description": self.description,
            "required_documents": self.required_documents,
            "safety_requirements": self.safety_requirements,
            "general_liability": self.general_liability,
            "auto_liability": self.auto_liability,
            "umbrella": self.umbrella,
            "workers_comp": self.workers_comp,
            "additional_insured": self.additional_insured,
            "waiver_of_subrogation": self.waiver_of_subrogation,
            "primary_non_contributory": self.primary_non_contributory,
            "renewal_required_days": self.renewal_required_days,
            "pending_renewal_days": self.pending_renewal_days,
        }