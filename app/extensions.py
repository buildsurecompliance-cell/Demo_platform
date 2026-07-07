from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler

db = SQLAlchemy()

login_manager = LoginManager()

login_manager.login_view = "login"

login_manager.login_message = (
    "Please log in to access this page."
)

scheduler = BackgroundScheduler()