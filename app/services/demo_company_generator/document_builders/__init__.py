from app.services.demo_company_generator.document_builders.coi_builder import (
    build_coi,
)
from app.services.demo_company_generator.document_builders.company_profile_builder import (
    build_company_profile,
)
from app.services.demo_company_generator.document_builders.contractor_license_builder import (
    build_contractor_license,
)
from app.services.demo_company_generator.document_builders.emr_letter_builder import (
    build_emr_letter,
)
from app.services.demo_company_generator.document_builders.osha_letter_builder import (
    build_osha_letter,
)
from app.services.demo_company_generator.document_builders.safety_manual_builder import (
    build_safety_manual,
)
from app.services.demo_company_generator.document_builders.scope_of_work_builder import (
    build_scope_of_work,
)
from app.services.demo_company_generator.document_builders.subcontract_agreement_builder import (
    build_subcontract_agreement,
)
from app.services.demo_company_generator.document_builders.vendor_form_builder import (
    build_vendor_form,
)
from app.services.demo_company_generator.document_builders.w9_builder import (
    build_w9,
)


DOCUMENT_BUILDERS = (
    build_coi,
    build_w9,
    build_safety_manual,
    build_emr_letter,
    build_osha_letter,
    build_contractor_license,
    build_vendor_form,
    build_company_profile,
    build_scope_of_work,
    build_subcontract_agreement,
)


__all__ = [
    "DOCUMENT_BUILDERS",
]
