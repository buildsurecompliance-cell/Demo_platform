from .ai import ai_bp
from .auth import auth_bp
from .billing import billing_bp
from .dashboard import dashboard_bp
from .documents import documents_bp
from .health import health_bp
from .notifications import notifications_bp
from .organization_settings import organization_settings_bp
from .projects import projects_bp
from .subcontractors import subcontractors_bp
from .document_requests import document_requests_bp
from .team import team_bp

__all__ = [
    "ai_bp",
    "auth_bp",
    "billing_bp",
    "dashboard_bp",
    "documents_bp",
    "document_requests_bp",
    "health_bp",
    "notifications_bp",
    "organization_settings_bp",
    "projects_bp",
    "subcontractors_bp",
    "team_bp",
]
