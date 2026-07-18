from datetime import date, timedelta

from app.services.demo_company_generator.models import (
    DemoCompany,
    DemoInsurancePolicy,
    DemoScenario,
)


VALID_SCENARIOS = {
    "ready",
    "pending",
    "blocked",
}


def build_scenario(company: DemoCompany, scenario_key="ready"):
    key = (scenario_key or "ready").strip().lower()

    if key not in VALID_SCENARIOS:
        raise ValueError("Invalid demo scenario.")

    if key == "ready":
        policy = _policy(
            company=company,
            effective_date=date(2026, 3, 1),
            expiration_date=date(2027, 3, 1),
            gl_each=2_000_000,
            gl_aggregate=4_000_000,
            auto=1_000_000,
            umbrella=5_000_000,
            additional_insured=True,
            waiver=True,
        )
        return DemoScenario(
            key="ready",
            label="READY",
            expected_status="READY",
            required_coverage=2_000_000,
            policy=policy,
            notes=("COI valid beyond 30 days with sufficient coverage.",),
        )

    if key == "pending":
        policy = _policy(
            company=company,
            effective_date=date.today() - timedelta(days=300),
            expiration_date=date.today() + timedelta(days=15),
            gl_each=2_000_000,
            gl_aggregate=4_000_000,
            auto=1_000_000,
            umbrella=5_000_000,
            additional_insured=True,
            waiver=True,
        )
        return DemoScenario(
            key="pending",
            label="PENDING",
            expected_status="PENDING",
            required_coverage=2_000_000,
            policy=policy,
            missing_secondary_document=True,
            notes=("COI expires within 30 days.",),
        )

    policy = _policy(
        company=company,
        effective_date=date.today() - timedelta(days=420),
        expiration_date=date.today() - timedelta(days=5),
        gl_each=1_000_000,
        gl_aggregate=2_000_000,
        auto=1_000_000,
        umbrella=0,
        additional_insured=False,
        waiver=False,
    )
    return DemoScenario(
        key="blocked",
        label="BLOCKED",
        expected_status="BLOCKED",
        required_coverage=2_000_000,
        policy=policy,
        notes=("COI expired and coverage is below project requirement.",),
    )


def _policy(
    *,
    company,
    effective_date,
    expiration_date,
    gl_each,
    gl_aggregate,
    auto,
    umbrella,
    additional_insured,
    waiver,
):
    return DemoInsurancePolicy(
        carrier_name="Lone Star Mutual Demo Insurance",
        producer_name="Bluebonnet Risk Advisors Demo Agency",
        policy_number=f"GL-{company.license_number[-4:]}-2026",
        effective_date=effective_date,
        expiration_date=expiration_date,
        general_liability_each_occurrence=gl_each,
        general_liability_aggregate=gl_aggregate,
        auto_liability=auto,
        umbrella_each_occurrence=umbrella,
        workers_compensation="Statutory",
        additional_insured=additional_insured,
        waiver_of_subrogation=waiver,
    )
