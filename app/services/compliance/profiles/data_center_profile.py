from app.services.compliance.profiles.base_profile import ComplianceProfile


def get_data_center_profile():

    return ComplianceProfile(
        name="Data Center",
        description="High-risk long-duration project profile for data centers.",
        required_documents=[
            "Certificate of Insurance",
            "W-9",
            "Business License",
            "OSHA",
            "Safety Training",
            "Background Check",
            "Drug Test",
        ],
        safety_requirements=[
            "OSHA 10",
            "Site Safety Orientation",
            "Lockout/Tagout Training",
        ],
        general_liability=2000000,
        auto_liability=1000000,
        umbrella=5000000,
        workers_comp=True,
        additional_insured=True,
        waiver_of_subrogation=True,
        primary_non_contributory=True,
        renewal_required_days=180,
        pending_renewal_days=30,
    )