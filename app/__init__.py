import os
import logging

from flask import Flask
from flask_login import current_user

from app.config import get_config

from app.extensions import (
    csrf,
    db,
    login_manager,
    migrate,
)
from app.security import register_security

from app.routes import (
    ai_bp,
    auth_bp,
    dashboard_bp,
    documents_bp,
    health_bp,
    notifications_bp,
    projects_bp,
    subcontractors_bp,
    team_bp,
)

from app.utils import register_template_filters


def create_app(config_object=None):

    app = Flask(__name__)

    app.config.from_object(
        config_object
        or get_config()
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if app.config.get("STORAGE_BACKEND", "local").lower() == "local":
        os.makedirs(
            app.config["UPLOAD_FOLDER"],
            exist_ok=True
        )

    db.init_app(app)

    migrate.init_app(app, db)

    csrf.init_app(app)

    login_manager.init_app(app)

    register_template_filters(app)
    register_security(app)

    @app.context_processor
    def organization_context():
        if not current_user.is_authenticated:
            return {
                "active_organization": None,
            }

        from app.services.organizations import get_current_organization

        return {
            "active_organization": get_current_organization(),
        }

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(subcontractors_bp)
    app.register_blueprint(team_bp)
    app.register_blueprint(ai_bp)

    from app import models

    return app
