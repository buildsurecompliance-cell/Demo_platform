from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler
from flask_migrate import Migrate
from flask_wtf import CSRFProtect

migrate = Migrate()
db = SQLAlchemy()
csrf = CSRFProtect()

login_manager = LoginManager()

login_manager.login_view = "auth.login"

login_manager.login_message = (
    "Please log in to access this page."
)

scheduler = BackgroundScheduler()
