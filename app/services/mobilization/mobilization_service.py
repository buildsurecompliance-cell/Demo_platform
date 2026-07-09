from datetime import (
    date,
    datetime,
)


# ==========================
# MOBILIZATION STATUS LOGIC
# ==========================

def calculate_mobilization_status(project):

    today = date.today()

    if not project.subs:
        return "Not Cleared"

    has_pending = False

    for ps in project.subs:

        sub = ps.subcontractor

        if not sub:
            return "Not Cleared"

        expiration = sub.coi_expiration

        if not expiration:
            return "Not Cleared"

        if isinstance(expiration, datetime):
            expiration = expiration.date()

        if expiration < today:
            return "Not Cleared"

        if project.end_date and expiration < project.end_date:
            return "Not Cleared"

        required = getattr(
            project,
            "required_coverage",
            None
        )

        if required:

            coverage = ps.coverage_limit or 0

            if coverage < required:
                return "Not Cleared"

        days_left = (
            expiration - today
        ).days

        if days_left <= 30:
            has_pending = True

    if has_pending:
        return "Pending Compliance"

    return "Ready to Mobilize"