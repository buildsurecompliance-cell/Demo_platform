from app.services.compliance.profile_merger import (
    merge_trade_and_profile,
)

from app.services.compliance.risk_engine import (
    build_risk_summary,
    calculate_risk_level,
)

from app.services.compliance.renewal_engine import (
    evaluate_renewal_status,
)

from app.services.compliance.validator import (
    validate_coi,
)


def evaluate_document_compliance(
    coi_data,
    trade=None,
    profile=None,
    project_end_date=None,
):

    config = merge_trade_and_profile(
        trade=trade,
        profile_name=profile,
    )

    requirements = config["requirements"]

    decision = validate_coi(
        requirements,
        coi_data,
    )

    renewal = evaluate_renewal_status(
        expiration_date=coi_data.get(
            "expiration_date"
        ),
        project_end_date=project_end_date,
        renewal_required_days=config[
            "renewal_required_days"
        ],
        pending_renewal_days=config[
            "pending_renewal_days"
        ],
    )

    risk_level = calculate_risk_level(
        decision
    )

    risk_summary = build_risk_summary(
        decision
    )

    return {

        "trade": config["trade"],

        "profile": config["profile"],

        "status": renewal["status"]
        if decision.status == "Ready"
        else decision.status,

        "score": decision.score,

        "risk_level": risk_level,

        "risk_summary": risk_summary,

        "renewal": renewal,

        "required_documents": config[
            "required_documents"
        ],

        "required_endorsements": config[
            "required_endorsements"
        ],

        "safety_requirements": config[
            "safety_requirements"
        ],

        "issues": [
            i.__dict__
            for i in decision.issues
        ],

        "warnings": [
            w.__dict__
            for w in decision.warnings
        ],

        "extracted_data": decision.extracted_data,
    }