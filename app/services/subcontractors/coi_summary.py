from dataclasses import dataclass
from datetime import date

from app.services.compliance_evidence_service import collect_coi_evidence
from app.services.documents.types import SUBCONTRACTOR_DOCUMENT_TYPE


@dataclass(frozen=True)
class SubcontractorCoiSummary:
    status: str
    expiration: date | None = None
    coverage: float | int | None = None

    @property
    def has_coverage(self):
        return self.coverage is not None


def get_subcontractor_coi_summary(subcontractor):
    if _has_processing_coi_document(subcontractor):
        return SubcontractorCoiSummary(status="CHECKING")

    validated_evidence = [
        item
        for item in collect_coi_evidence(subcontractor)
        if item.validated
    ]

    if validated_evidence:
        evidence = validated_evidence[0]
        expiration = evidence.value.get("expiration_date")
        status = "EXPIRED" if _is_expired(expiration) else "VALID"

        return SubcontractorCoiSummary(
            status=status,
            expiration=expiration,
            coverage=evidence.value.get("coverage"),
        )

    expiration = getattr(subcontractor, "coi_expiration", None)

    if not expiration:
        return SubcontractorCoiSummary(status="MISSING")

    status = "EXPIRED" if _is_expired(expiration) else "VALID"

    return SubcontractorCoiSummary(
        status=status,
        expiration=expiration,
    )


def _has_processing_coi_document(subcontractor):
    for document in getattr(subcontractor, "documents", []) or []:
        if document.document_type != SUBCONTRACTOR_DOCUMENT_TYPE:
            continue

        if document.ai_status not in {
            "analyzed",
            "failed",
        }:
            return True

    return False


def _is_expired(expiration):
    return bool(
        expiration
        and expiration < date.today()
    )
