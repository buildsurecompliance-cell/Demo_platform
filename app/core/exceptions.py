class BuildSureError(Exception):
    pass


class ComplianceError(BuildSureError):
    pass


class DocumentAnalysisError(BuildSureError):
    pass


class AIProviderError(BuildSureError):
    pass


class UnauthorizedDocumentAccess(BuildSureError):
    pass