from app.services.compliance.packages.base_package import CompliancePackage


def get_mechanical_package():

    return CompliancePackage(
        trade="Mechanical",
        required_documents=[
            "Certificate of Insurance",
            "W-9",
            "Business License",
            "Mechanical License",
        ],
        required_endorsements=[
            "Additional Insured",
            "Waiver of Subrogation",
            "Primary and Non-Contributory",
        ],
        general_liability=2000000,
        auto_liability=1000000,
        umbrella=3000000,
        workers_comp=True,
        safety_requirements=[
            "OSHA 10",
            "Lockout/Tagout Training",
            "Hot Work Safety",
            "Site Safety Orientation",
        ],
    )