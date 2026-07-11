from app.services.readiness_service import (
    BLOCKED,
    PENDING,
    calculate_readiness,
)


# ==========================
# MOBILIZATION STATUS LOGIC
# ==========================

def calculate_mobilization_status(project):

    if not project.subs:
        return "Not Cleared"

    statuses = []

    for ps in project.subs:

        readiness = calculate_readiness(ps)
        statuses.append(readiness["status"])

    if BLOCKED in statuses:
        return "Not Cleared"

    if PENDING in statuses:
        return "Pending Compliance"

    return "Ready to Mobilize"
