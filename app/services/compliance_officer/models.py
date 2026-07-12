from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ComplianceAction:
    title: str
    description: str
    priority: str
    blocking: bool

    def to_dict(self):
        return {
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "blocking": self.blocking,
        }


@dataclass(frozen=True)
class ComplianceAdvice:
    status: str
    summary: str
    reasons: tuple[dict[str, Any], ...]
    actions: tuple[ComplianceAction, ...]
    priority: str
    confidence: float
    generated_at: datetime

    def to_dict(self):
        return {
            "status": self.status,
            "summary": self.summary,
            "reasons": list(self.reasons),
            "actions": [
                action.to_dict()
                for action in self.actions
            ],
            "priority": self.priority,
            "confidence": self.confidence,
            "generated_at": self.generated_at.isoformat(),
        }
