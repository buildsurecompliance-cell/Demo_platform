from dataclasses import dataclass, field


@dataclass
class CompliancePackage:
    trade: str
    required_documents: list[str] = field(default_factory=list)
    required_endorsements: list[str] = field(default_factory=list)

    general_liability: int = 1000000
    auto_liability: int = 1000000
    umbrella: int = 0
    workers_comp: bool = True

    safety_requirements: list[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "trade": self.trade,
            "required_documents": self.required_documents,
            "required_endorsements": self.required_endorsements,
            "general_liability": self.general_liability,
            "auto_liability": self.auto_liability,
            "umbrella": self.umbrella,
            "workers_comp": self.workers_comp,
            "safety_requirements": self.safety_requirements,
        }