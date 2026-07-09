from app.services.compliance.packages.base_package import CompliancePackage


def get_painting_package():

    return CompliancePackage(
        trade="Painting",
        required_documents=[
            "Certificate of Insurance",
            "W-9",
            "Business License",
        ],
        required_endorsements=[
            "Additional Insured",
            "Waiver of Subrogation",
        ],
        general_liability=1000000,
        auto_liability=1000000,
        umbrella=0,
        workers_comp=True,
        safety_requirements=[
            "Site Safety Orientation",
        ],
    )