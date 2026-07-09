from app.services.compliance.profiles.base_profile import ComplianceProfile


def get_commercial_profile():

    return ComplianceProfile(
        name="Commercial Building",
        description="Default commercial construction compliance profile.",
        required_documents=[
            "Certificate of Insurance",
            "W-9",
            "Business License",
        ],
        safety_requirements=[
            "Site Safety Orientation",
        ],
        general_liability=2000000,
        auto_liability=1000000,
        umbrella=2000000,
        workers_comp=True,
        additional_insured=True,
        waiver_of_subrogation=True,
        primary_non_contributory=True,
        renewal_required_days=180,
        pending_renewal_days=30,
    )