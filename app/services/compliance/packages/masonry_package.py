from app.services.compliance.packages.base_package import CompliancePackage


def get_masonry_package():

    return CompliancePackage(
        trade="Masonry",
        required_documents=[
            "Certificate of Insurance",
            "W-9",
            "Business License",
        ],
        required_endorsements=[
            "Additional Insured",
            "Waiver of Subrogation",
        ],
        general_liability=2000000,
        auto_liability=1000000,
        umbrella=1000000,
        workers_comp=True,
        safety_requirements=[
            "OSHA 10",
            "Silica Awareness",
            "Scaffold Safety",
            "Site Safety Orientation",
        ],
    )