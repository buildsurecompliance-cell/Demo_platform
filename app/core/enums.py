from enum import Enum


class ComplianceStatus(str, Enum):
    READY = "Ready"
    READY_RENEWAL_REQUIRED = "Ready - Renewal Required"
    PENDING_RENEWAL = "Pending Renewal"
    PENDING = "Pending"
    BLOCKED = "Blocked"


class RiskLevel(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class DocumentAIStatus(str, Enum):
    NOT_ANALYZED = "not_analyzed"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    FAILED = "failed"


class DocumentType(str, Enum):
    COI = "Certificate of Insurance"
    W9 = "W-9"
    LICENSE = "License"
    CONTRACT = "Contract"
    OTHER = "Other"