from app.services.compliance_profiles.models import (
    ComplianceProfile,
    ComplianceRequirement,
)


DEFAULT_SUBCONTRACTOR_PROFILE_KEY = "DEFAULT_SUBCONTRACTOR"

DEFAULT_SUBCONTRACTOR_PROFILE = ComplianceProfile(
    key=DEFAULT_SUBCONTRACTOR_PROFILE_KEY,
    name="Default Subcontractor Compliance",
    description=(
        "Default compliance profile for subcontractors. "
        "Requires only Certificate of Insurance in V1."
    ),
    requirements=(
        ComplianceRequirement(
            document_type="COI",
            required=True,
            blocking=True,
            description="Certificate of Insurance / Insurance document.",
        ),
    ),
)


def get_compliance_profile(project_subcontractor):
    return DEFAULT_SUBCONTRACTOR_PROFILE
