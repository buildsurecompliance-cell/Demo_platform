PROJECT_DOCUMENT_TYPES = (
    "Contract",
    "Scope",
    "Owner Requirements",
)

DEFAULT_PROJECT_DOCUMENT_TYPE = PROJECT_DOCUMENT_TYPES[0]
SUBCONTRACTOR_DOCUMENT_TYPE = "COI"

AUTOMATIC_ANALYSIS_DOCUMENT_TYPES = frozenset(
    {
        "COI",
        "Contract",
    }
)

_PROJECT_DOCUMENT_TYPE_ALIASES = {
    "prime contract": "Contract",
    "subcontract": "Contract",
    "subcontract agreement": "Contract",
    "change order": "Contract",
    "scope of work": "Scope",
    "specifications": "Scope",
    "drawings": "Scope",
    "project schedule": "Scope",
    "owner requirement": "Owner Requirements",
    "owner requirements": "Owner Requirements",
}


def normalize_project_document_type(document_type):
    if document_type in PROJECT_DOCUMENT_TYPES:
        return document_type

    value = (document_type or "").strip().lower()

    if value in _PROJECT_DOCUMENT_TYPE_ALIASES:
        return _PROJECT_DOCUMENT_TYPE_ALIASES[value]

    return DEFAULT_PROJECT_DOCUMENT_TYPE


def supports_automatic_analysis(document_type):
    return document_type in AUTOMATIC_ANALYSIS_DOCUMENT_TYPES
