from app.services.compliance.packages.base_package import CompliancePackage


def get_civil_package():

    return CompliancePackage(
        trade="Civil",
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
        umbrella=2000000,
        workers_comp=True,
        safety_requirements=[
            "OSHA 10",
            "Excavation Safety",
            "Site Safety Orientation",
        ],
    )