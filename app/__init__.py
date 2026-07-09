import os

from flask import Flask

from app.config import Config

from app.extensions import (
    db,
    login_manager,
)

from app.routes import (
    ai_bp,
    auth_bp,
    dashboard_bp,
    documents_bp,
    notifications_bp,
    projects_bp,
    subcontractors_bp,
)

from app.utils import register_template_filters


def create_app():

    app = Flask(__name__)

    app.config.from_object(Config)

    os.makedirs(
        app.config["UPLOAD_FOLDER"],
        exist_ok=True
    )

    db.init_app(app)

    login_manager.init_app(app)

    register_template_filters(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(subcontractors_bp)
    app.register_blueprint(ai_bp)

    with app.app_context():

        from app import models

        db.create_all()

    return app