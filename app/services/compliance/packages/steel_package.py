from app.services.compliance.packages.base_package import CompliancePackage


def get_steel_package():

    return CompliancePackage(
        trade="Steel",
        required_documents=[
            "Certificate of Insurance",
            "W-9",
            "Business License",
            "Welding Certification",
            "Crane Certification",
        ],
        required_endorsements=[
            "Additional Insured",
            "Waiver of Subrogation",
            "Primary and Non-Contributory",
        ],
        general_liability=2000000,
        auto_liability=1000000,
        umbrella=10000000,
        workers_comp=True,
        safety_requirements=[
            "OSHA 30",
            "Fall Protection",
            "Lift Certification",
            "Site Safety Orientation",
        ],
    )