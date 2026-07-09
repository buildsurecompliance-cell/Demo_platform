from dataclasses import dataclass, field
from typing import Any


@dataclass
class DocumentIntelligenceResult:

    document_type: str
    status: str
    score: int = 100
    extracted_data: dict[str, Any] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    summary: str = ""

    def to_dict(self):

        return {
            "document_type": self.document_type,
            "status": self.status,
            "score": self.score,
            "extracted_data": self.extracted_data,
            "issues": self.issues,
            "warnings": self.warnings,
            "confidence": self.confidence,
            "summary": self.summary,
        }