from .auth import auth_bp
from .dashboard import dashboard_bp
from .documents import documents_bp
from .notifications import notifications_bp
from .projects import projects_bp
from .subcontractors import subcontractors_bp

__all__ = [
    "auth_bp",
    "dashboard_bp",
    "documents_bp",
    "notifications_bp",
    "projects_bp",
    "subcontractors_bp",
]