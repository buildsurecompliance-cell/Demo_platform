from datetime import date, datetime


def parse_date(value):

    if not value:
        return None

    if isinstance(value, date):
        return value

    try:
        return datetime.strptime(
            value,
            "%Y-%m-%d"
        ).date()

    except Exception:
        return None


def evaluate_coi_data(coi_data, project=None):

    reasons = []
    warnings = []

    expiration_date = parse_date(
        coi_data.get("expiration_date")
    )

    if not expiration_date:
        reasons.append("Expiration date not found.")

    else:
        today = date.today()

        if expiration_date < today:
            reasons.append("COI is expired.")

        elif (expiration_date - today).days <= 30:
            warnings.append("COI expires within 30 days.")

        if project and project.end_date:
            if expiration_date < project.end_date:
                reasons.append(
                    "COI expires before the project end date."
                )

    if not coi_data.get("named_insured"):
        reasons.append("Named insured not found.")

    if not coi_data.get("general_liability_limit"):
        warnings.append("General Liability limit not found.")

    if not coi_data.get("workers_compensation"):
        warnings.append("Workers Compensation not confirmed.")

    if not coi_data.get("additional_insured"):
        warnings.append("Additional Insured not confirmed.")

    if not coi_data.get("waiver_of_subrogation"):
        warnings.append("Waiver of Subrogation not confirmed.")

    if reasons:
        status = "Blocked"

    elif warnings:
        status = "Pending"

    else:
        status = "Ready"

    return {
        "status": status,
        "reasons": reasons,
        "warnings": warnings,
        "expiration_date": (
            expiration_date.isoformat()
            if expiration_date
            else None
        ),
    }