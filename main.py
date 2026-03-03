# ==========================================================
# COI TRACKER SaaS - Main Application
# Production-Ready Structure
# ==========================================================

from flask import Flask, render_template, redirect, url_for, request, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, date, timezone
from apscheduler.schedulers.background import BackgroundScheduler
import os
import pytz
import requests

# ==========================================================
# CONFIGURATION
# ==========================================================

app = Flask(__name__)

# Security
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "dev-secret-key")

# Database
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    "DATABASE_URL", "sqlite:///database.db"
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Upload folder
UPLOAD_FOLDER = os.path.join('static', 'docs')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Email API Key (Resend)
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")

# ==========================================================
# INITIALIZATION
# ==========================================================

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "warning"

# ==========================================================
# DATABASE MODELS
# ==========================================================

class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password = db.Column(db.String(200), nullable=False)
    timezone = db.Column(db.String(50), default="US/Eastern")
    paid = db.Column(db.Boolean, default=False)

    subs = db.relationship('Subcontractor', backref='owner', lazy=True, cascade="all, delete-orphan")

class Subcontractor(db.Model):
    __tablename__ = "subcontractors"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    role = db.Column(db.String(100))
    coi_expiration = db.Column(db.Date)
    last_reminder_sent = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default="compliant")
    timezone = db.Column(db.String(50), default="US/Eastern")

    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    documents = db.relationship('Document', backref='sub', lazy=True, cascade="all, delete-orphan")

class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200))
    type = db.Column(db.String(50))

    sub_id = db.Column(db.Integer, db.ForeignKey('subcontractors.id'))

# ==========================================================
# LOGIN MANAGER
# ==========================================================

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# ==========================================================
# TEMPLATE FILTERS
# ==========================================================

@app.template_filter('format_local_time')
def format_local_time(value, tz_name="US/Eastern"):
    if not value:
        return ""
    try:
        tz = pytz.timezone(tz_name)
        return value.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return value.strftime("%Y-%m-%d %H:%M")

# ==========================================================
# EMAIL SYSTEM (Resend API)
# ==========================================================

def send_email_reminder(to_email, subject, message):

    if not RESEND_API_KEY:
        print("⚠ RESEND_API_KEY not configured")
        return False

    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": "COI Tracker <onboarding@resend.dev>",
            "to": to_email,
            "subject": subject,
            "html": f"<p>{message}</p>",
        },
        timeout=10
    )

    print("Email status:", response.status_code)
    return response.status_code == 200

# ==========================================================
# AUTOMATED DAILY REMINDERS
# ==========================================================

def check_and_send_auto_reminders_for_all_users():
    today = date.today()
    users = User.query.all()

    for user in users:
        subs = Subcontractor.query.filter_by(user_id=user.id).all()

        for sub in subs:
            if sub.coi_expiration:
                days_left = (sub.coi_expiration - today).days

                if sub.last_reminder_sent and sub.last_reminder_sent.date() == today:
                    continue

                if days_left in [45, 30, 15]:
                    subject = "COI Expiration Reminder"
                    message = f"""
                    Hello {sub.name},<br><br>
                    Your Certificate of Insurance (COI) will expire on {sub.coi_expiration}.<br>
                    Please upload the updated certificate to remain compliant.<br><br>
                    – COI Tracker
                    """

                    sent = send_email_reminder(sub.email, subject, message)

                    if sent:
                        sub.last_reminder_sent = datetime.now(timezone.utc)
                        db.session.commit()
                        print(f"Reminder sent to {sub.email}")

# ==========================================================
# ROUTES
# ==========================================================

@app.route("/")
def home():
    return redirect(url_for("subscribe"))

# --------------------------
# SUBSCRIBE
# --------------------------

@app.route("/subscribe", methods=["GET","POST"])
def subscribe():
    if request.method == "POST":
        email = request.form.get("email")
        flash("Payment successful! Now create your account.", "success")
        return redirect(url_for("register", email=email))
    return render_template("subscribe.html")

# --------------------------
# REGISTER
# --------------------------

@app.route("/register", methods=["GET","POST"])
def register():
    email_prefill = request.args.get("email", "")

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if User.query.filter_by(email=email).first():
            flash("Email already registered", "danger")
            return render_template("register.html", email_prefill=email)

        if not password or len(password) < 8:
            flash("Password must be at least 8 characters", "danger")
            return render_template("register.html", email_prefill=email)

        hashed = generate_password_hash(password)
        new_user = User(email=email, password=hashed, paid=True)

        db.session.add(new_user)
        db.session.commit()

        flash("Account created successfully!", "success")
        return redirect(url_for("login"))

    return render_template("register.html", email_prefill=email_prefill)

# --------------------------
# LOGIN
# --------------------------

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password, password):

            if not user.paid:
                flash("Subscription required.", "warning")
                return redirect(url_for("subscribe"))

            login_user(user)
            return redirect(url_for("dashboard"))

        flash("Invalid credentials", "danger")

    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# --------------------------
# DASHBOARD
# --------------------------

@app.route("/dashboard")
@login_required
def dashboard():

    subs = Subcontractor.query.filter_by(user_id=current_user.id).all()
    today = date.today()

    expired = []
    at_risk = []
    trend_counts = [0, 0, 0, 0]

    for sub in subs:
        if sub.coi_expiration:
            days_left = (sub.coi_expiration - today).days

            if days_left < 0:
                expired.append(sub)
                sub.status = "expired"
            elif days_left <= 30:
                at_risk.append(sub)
                sub.status = "risk"
            else:
                sub.status = "compliant"

            if 0 <= days_left <= 15:
                trend_counts[0] += 1
            elif 16 <= days_left <= 30:
                trend_counts[1] += 1
            elif 31 <= days_left <= 45:
                trend_counts[2] += 1
            elif 46 <= days_left <= 60:
                trend_counts[3] += 1

    db.session.commit()

    return render_template(
        "dashboard.html",
        subs=subs,
        expired=expired,
        at_risk=at_risk,
        trend_counts=trend_counts,
        today=today
    )

# ==========================================================
# SCHEDULER
# ==========================================================

def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        func=check_and_send_auto_reminders_for_all_users,
        trigger="cron",
        hour=8,
        minute=0
    )
    scheduler.start()

# ==========================================================
# APPLICATION START
# ==========================================================

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        start_scheduler()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)