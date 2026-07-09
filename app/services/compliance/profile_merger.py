from app.services.compliance.models import ProjectRequirements
from app.services.compliance.packages import get_package_for_trade
from app.services.compliance.profiles import get_profile


def merge_trade_and_profile(
    trade=None,
    profile_name=None,
):

    trade_package = get_package_for_trade(trade)
    profile = get_profile(profile_name)

    required_documents = sorted(
        set(trade_package.required_documents)
        | set(profile.required_documents)
    )

    safety_requirements = sorted(
        set(trade_package.safety_requirements)
        | set(profile.safety_requirements)
    )

    required_endorsements = sorted(
        set(trade_package.required_endorsements)
    )

    requirements = ProjectRequirements(
        general_liability=max(
            trade_package.general_liability,
            profile.general_liability,
        ),
        auto_liability=max(
            trade_package.auto_liability,
            profile.auto_liability,
        ),
        umbrella=max(
            trade_package.umbrella,
            profile.umbrella,
        ),
        workers_comp=(
            trade_package.workers_comp
            or profile.workers_comp
        ),
        additional_insured=profile.additional_insured,
        waiver_of_subrogation=profile.waiver_of_subrogation,
        primary_non_contributory=profile.primary_non_contributory,
        coi_must_cover_project_duration=False,
    )

    return {
        "trade": trade_package.trade,
        "profile": profile.name,
        "requirements": requirements,
        "required_documents": required_documents,
        "required_endorsements": required_endorsements,
        "safety_requirements": safety_requirements,
        "renewal_required_days": profile.renewal_required_days,
        "pending_renewal_days": profile.pending_renewal_days,
    }