def calculate_risk_level(decision):

    if decision.status == "Blocked":
        return "High"

    if decision.status == "Pending":
        return "Medium"

    if decision.score < 70:
        return "High"

    if decision.score < 90:
        return "Medium"

    return "Low"


def build_risk_summary(decision):

    risk_level = calculate_risk_level(decision)

    if risk_level == "High":

        if decision.issues:

            main_issue = decision.issues[0].message

            return (
                f"High Risk — {main_issue}"
            )

        return "High Risk — Compliance issues found."

    if risk_level == "Medium":

        if decision.warnings:

            main_warning = decision.warnings[0].message

            return (
                f"Medium Risk — {main_warning}"
            )

        return "Medium Risk — Review recommended."

    return "Low Risk — Ready to mobilize."