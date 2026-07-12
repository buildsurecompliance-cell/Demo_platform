from dataclasses import dataclass


@dataclass(frozen=True)
class ComplianceRequirement:
    document_type: str
    required: bool
    blocking: bool
    description: str


@dataclass(frozen=True)
class ComplianceProfile:
    key: str
    name: str
    description: str
    requirements: tuple[ComplianceRequirement, ...]
