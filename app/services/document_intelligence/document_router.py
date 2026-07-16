def normalize_document_type(document_type):

    if not document_type:
        return "other"

    value = (
        document_type
        .lower()
        .strip()
        .replace("_", " ")
        .replace("-", " ")
    )

    if value in [
        "contract",
        "prime contract",
        "project contract",
        "subcontract",
        "subcontract agreement",
        "agreement",
        "construction contract",
    ]:
        return "contract"

    if value in [
        "certificate of insurance",
        "coi",
        "insurance",
    ]:
        return "coi"

    if value in [
        "w 9",
        "w9",
        "tax form",
    ]:
        return "w9"

    if value in [
        "business license",
        "license",
        "licence",
    ]:
        return "license"

    if value in [
        "osha",
        "osha card",
        "osha certification",
    ]:
        return "osha"

    if value in [
        "drug test",
        "drug screening",
    ]:
        return "drug_test"

    if value in [
        "safety training",
        "safety",
        "training",
        "safety certificate",
    ]:
        return "safety_training"

    if value in [
        "background check",
        "background screening",
    ]:
        return "background_check"

    return "other"


def get_document_category(document):

    return normalize_document_type(
        getattr(
            document,
            "document_type",
            None
        )
    )
